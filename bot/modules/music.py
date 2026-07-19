"""
music - YouTube audio playback via yt-dlp, with SponsorBlock-based auto-skip
of non-music segments (sponsor reads, self-promo, intros/outros, and other
off-topic content baked into the video itself).

"Ad-free" here just means yt-dlp streams the raw audio-only CDN URL directly -
that bypasses the YouTube player's ad-insertion layer entirely, since a direct
stream extraction was never subject to it in the first place. SponsorBlock is
the separate, complementary piece: crowd-sourced timestamps for content that
*is* part of the video/audio stream, which we skip by restarting playback at
the segment's end time. Coverage depends on what's been submitted for a given
video - popular music videos are usually well covered, obscure ones may not be.

Config keys:
  music.sponsorblock_enabled - "true"/"false", default true

No queue/track state is persisted - it's in-memory per guild, like
antispam.py's flood-tracking history, and resets on restart.

Channel hygiene: there's one persistent "now playing" message per guild with
playback buttons attached (MusicControlView), always the most recent one -
it gets deleted and re-posted at the bottom of the channel every time a new
track starts. Transient action confirmations (skip/pause/etc.) use
delete_after so they don't linger. When the session ends (stop/leave/idle/
abandoned), the last "now playing" message stays as a recap but has its
buttons stripped.

Sources: anything yt-dlp has an extractor for (YouTube, SoundCloud,
Bandcamp, direct audio URLs, ...) plays straight from that source - yt-dlp
picks the right extractor from the URL automatically, no special-casing
needed here. Spotify is the one exception: yt-dlp has no Spotify extractor
at all, because Spotify streams are DRM-protected and there is no legal way
to pull raw audio from them. A Spotify link's *audio* was never playable
directly by any bot, so a Spotify URL always resolves via a different path:
look up the track's title/artist through Spotify's Web API (needs
SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET), then search YouTube for that and
play the result - same as typing a plain search query. Bare search text (no
URL) already goes straight to a YouTube search via yt-dlp's default_search.

Playlists: a bare YouTube/Spotify playlist link (or a video link that's part
of a playlist, e.g. `watch?v=X&list=Y`) queues multiple tracks. Since a
video-in-playlist link is ambiguous (did you mean just that video, or the
whole thing it's part of?), /play prompts with buttons in that one case;
a bare playlist link has no such ambiguity and is queued directly. Playlist
entries are resolved lazily (just title/URL up front, the real playable
stream fetched right before each one plays) so queuing a long playlist is
fast and a handful of dead entries don't block the rest - each one that
fails to resolve is skipped with a brief note instead of stopping playback.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import aiohttp
import discord
import yt_dlp
from discord.ext import commands, tasks

logger = logging.getLogger("bot.modules.music")

SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?track/([a-zA-Z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/(?:intl-[a-zA-Z-]+/)?playlist/([a-zA-Z0-9]+)")
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")
PLAYLIST_MAX_TRACKS = 200

SPONSORBLOCK_API = "https://sponsor.ajay.app/api/skipSegments"
# "Non-music and other unneeded stuff": sponsor reads, self-promo, intros/
# outros, and music_offtopic (the non-music portion of a music video).
SPONSORBLOCK_CATEGORIES = [
    "sponsor",
    "selfpromo",
    "interaction",
    "intro",
    "outro",
    "preview",
    "filler",
    "music_offtopic",
]

YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch1",
    "quiet": True,
    "no_warnings": True,
}

# Lightweight enumeration of a playlist's entries (title/id/url only) without
# resolving every single video's real audio stream up front - resolving a
# long playlist eagerly would be slow and those signed stream URLs expire
# anyway, so each entry is resolved for real right before it actually plays.
PLAYLIST_YDL_OPTS = {
    "extract_flat": "in_playlist",
    "quiet": True,
    "no_warnings": True,
}

FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"

IDLE_DISCONNECT_SECONDS = 120
POSITION_CHECK_INTERVAL = 1
TRANSIENT_MESSAGE_SECONDS = 5
SEEK_STEP_SECONDS = 10.0


def extract_spotify_track_id(query: str) -> Optional[str]:
    """Returns the track ID if query is an open.spotify.com track link, else None."""
    match = SPOTIFY_TRACK_RE.search(query)
    return match.group(1) if match else None


def extract_spotify_playlist_id(query: str) -> Optional[str]:
    """Returns the playlist ID if query is an open.spotify.com playlist link, else None."""
    match = SPOTIFY_PLAYLIST_RE.search(query)
    return match.group(1) if match else None


def classify_youtube_link(query: str) -> str:
    """Classifies a query as "single" (plain search / one video, no playlist
    involved), "playlist" (a bare playlist link - unambiguous), or
    "video_in_playlist" (a specific video that's also part of a playlist -
    ambiguous, the caller should ask which one was meant)."""
    if not any(domain in query for domain in YOUTUBE_DOMAINS):
        return "single"
    if not re.search(r"[?&]list=", query):
        return "single"
    if "/playlist" in query and not re.search(r"[?&]v=", query):
        return "playlist"
    return "video_in_playlist"


def merge_skip_segments(segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sorts and merges overlapping/adjacent (start, end) ranges from the raw
    SponsorBlock response, so next_skip_target only has to check disjoint ranges."""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: s[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def next_skip_target(position: float, segments: list[tuple[float, float]]) -> Optional[float]:
    """If position falls inside one of the (already-merged) segments, returns
    that segment's end time to seek to. Otherwise None."""
    for start, end in segments:
        if start <= position < end:
            return end
    return None


def clamp_seek_target(position: float, delta: float, duration: Optional[float]) -> float:
    """Position + delta (manual rewind/forward), clamped to the track's
    bounds - never negative, and never past the end if duration is known
    (seeking exactly to/past EOF would just cut the track off early)."""
    target = position + delta
    if target < 0:
        target = 0.0
    if duration is not None and target > duration:
        target = max(duration - 1, 0.0)
    return target


class NoResultsError(Exception):
    """Raised when a search or URL resolves to zero playable entries."""


class SpotifyUnavailableError(NoResultsError):
    """Raised for a Spotify link when SPOTIFY_CLIENT_ID/SECRET aren't configured."""


@dataclass
class Track:
    title: str
    webpage_url: str
    requester_id: int
    stream_url: Optional[str] = None
    video_id: Optional[str] = None
    duration: Optional[float] = None
    # For a playlist entry queued before its real stream URL is known - what
    # to resolve (a video URL, or a "artist - title" search string for a
    # Spotify-sourced entry) right before it's about to play.
    resolve_query: Optional[str] = None


class GuildMusicState:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: deque[Track] = deque()
        self.voice_client: Optional[discord.VoiceClient] = None
        self.current: Optional[Track] = None
        self.skip_segments: list[tuple[float, float]] = []
        self.sponsorblock_enabled: bool = True
        self.play_started_at: Optional[float] = None
        self.seek_offset: float = 0.0
        self.idle_task: Optional[asyncio.Task] = None
        # Bumped whenever playback is interrupted for a reason that should
        # NOT advance the queue (SponsorBlock seek, /stop). The after-play
        # callback captures the generation active when it started and
        # no-ops if it no longer matches - this is what tells a stale
        # callback (from the track we just interrupted) apart from a
        # legitimate "this track really finished" callback.
        self.generation: int = 0
        # Channel hygiene / UI state.
        self.text_channel: Optional[discord.abc.Messageable] = None
        self.player_message: Optional[discord.Message] = None
        self.view: Optional["MusicControlView"] = None
        self.last_requester_id: Optional[int] = None
        self.ping_last_requester: bool = True

    def position(self) -> float:
        if self.play_started_at is None:
            return 0.0
        return (time.monotonic() - self.play_started_at) + self.seek_offset


class MusicControlView(discord.ui.View):
    def __init__(self, cog: "Music", guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self._sync_ping_button()

    def _state(self) -> GuildMusicState:
        return self.cog._state(self.guild_id)

    def _sync_ping_button(self) -> None:
        state = self._state()
        if state.ping_last_requester:
            self.ping_toggle.label = "Ping on empty: On"
            self.ping_toggle.emoji = "\N{BELL}"
        else:
            self.ping_toggle.label = "Ping on empty: Off"
            self.ping_toggle.emoji = "\N{BELL WITH CANCELLATION STROKE}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        state = self._state()
        if state.voice_client is None:
            await interaction.response.send_message("I'm not in a voice channel anymore.", ephemeral=True)
            return False
        voice = getattr(interaction.user, "voice", None)
        if voice is None or voice.channel is None or voice.channel.id != state.voice_client.channel.id:
            await interaction.response.send_message(
                "You need to be in my voice channel to do that.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="-10s", emoji="\N{BLACK LEFT-POINTING DOUBLE TRIANGLE}", row=0)
    async def rewind(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.current is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        target = clamp_seek_target(state.position(), -SEEK_STEP_SECONDS, state.current.duration)
        await interaction.response.send_message(f"Rewound to {int(target)}s.", ephemeral=True)
        await self.cog._seek_within_track(state, target)

    @discord.ui.button(
        label="Pause/Resume",
        emoji="\N{BLACK RIGHT-POINTING TRIANGLE WITH DOUBLE VERTICAL BAR}",
        row=0,
    )
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.voice_client.is_paused():
            state.voice_client.resume()
            await interaction.response.send_message("Resumed.", ephemeral=True)
        elif state.voice_client.is_playing():
            state.voice_client.pause()
            await interaction.response.send_message("Paused.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="+10s", emoji="\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE}", row=0)
    async def forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.current is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        target = clamp_seek_target(state.position(), SEEK_STEP_SECONDS, state.current.duration)
        await interaction.response.send_message(f"Jumped to {int(target)}s.", ephemeral=True)
        await self.cog._seek_within_track(state, target)

    @discord.ui.button(
        label="Skip", emoji="\N{BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR}", row=0
    )
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.current is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        state.voice_client.stop()
        await interaction.response.send_message("Skipped.", ephemeral=True)

    @discord.ui.button(label="Shuffle", emoji="\N{TWISTED RIGHTWARDS ARROWS}", row=0)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if len(state.queue) < 2:
            await interaction.response.send_message("Not enough songs queued to shuffle.", ephemeral=True)
            return
        shuffled = list(state.queue)
        random.shuffle(shuffled)
        state.queue = deque(shuffled)
        listing = self.cog._format_queue_listing(state)
        await interaction.response.send_message(f"Queue shuffled.\n{listing}", ephemeral=True)

    @discord.ui.button(
        label="End Session", emoji="\N{BLACK SQUARE FOR STOP}", style=discord.ButtonStyle.danger, row=1
    )
    async def end_session(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Session ended.", ephemeral=True)
        await self.cog._full_stop(self._state())

    @discord.ui.button(label="Queue", emoji="\N{BOOKMARK TABS}", row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        await interaction.response.send_message(self.cog._format_queue_listing(state), ephemeral=True)

    @discord.ui.button(label="Ping on empty: On", emoji="\N{BELL}", row=1)
    async def ping_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        state.ping_last_requester = not state.ping_last_requester
        self._sync_ping_button()
        await interaction.response.edit_message(view=self)


class PlaylistChoiceView(discord.ui.View):
    """Just this song, or the whole playlist? Only the person who ran /play
    can answer; the prompt message gets deleted right after either way, so
    there's no need to disable buttons or edit it in place first."""

    def __init__(self, author_id: int, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.choice: Optional[str] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This prompt isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Just this song", style=discord.ButtonStyle.primary)
    async def single(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "single"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Whole playlist", style=discord.ButtonStyle.secondary)
    async def playlist(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = "playlist"
        await interaction.response.defer()
        self.stop()


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        self.states: dict[int, GuildMusicState] = {}
        self._spotify_token: Optional[str] = None
        self._spotify_token_expires_at: float = 0.0
        self.position_check.start()

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        self.position_check.cancel()
        if self.session is not None:
            await self.session.close()
        for state in self.states.values():
            if state.voice_client is not None:
                await state.voice_client.disconnect(force=True)

    def _state(self, guild_id: int) -> GuildMusicState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildMusicState(guild_id)
        return self.states[guild_id]

    # ---- Spotify (metadata lookup only - see module docstring for why) ----

    async def _spotify_access_token(self) -> Optional[str]:
        client_id = os.getenv("SPOTIFY_CLIENT_ID")
        client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        if not client_id or not client_secret or self.session is None:
            return None
        if self._spotify_token and time.monotonic() < self._spotify_token_expires_at:
            return self._spotify_token
        try:
            async with self.session.post(
                SPOTIFY_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(client_id, client_secret),
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            logger.warning("Spotify token request failed", exc_info=True)
            return None
        self._spotify_token = data["access_token"]
        # Refresh a bit early so a token doesn't expire mid-lookup.
        self._spotify_token_expires_at = time.monotonic() + data.get("expires_in", 3600) - 30
        return self._spotify_token

    async def _spotify_search_query(self, track_id: str) -> Optional[str]:
        token = await self._spotify_access_token()
        if token is None or self.session is None:
            return None
        try:
            async with self.session.get(
                f"{SPOTIFY_API_BASE}/tracks/{track_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            logger.warning("Spotify track lookup failed for %s", track_id, exc_info=True)
            return None
        artists = ", ".join(a["name"] for a in data.get("artists", []))
        title = data.get("name", "")
        query = f"{artists} - {title}".strip(" -")
        return query or None

    # ---- track resolution ----

    async def _resolve(self, query: str, requester_id: int) -> Track:
        spotify_track_id = extract_spotify_track_id(query)
        if spotify_track_id:
            if not os.getenv("SPOTIFY_CLIENT_ID") or not os.getenv("SPOTIFY_CLIENT_SECRET"):
                raise SpotifyUnavailableError("Spotify credentials not configured")
            search_query = await self._spotify_search_query(spotify_track_id)
            if search_query is None:
                raise NoResultsError(f"Spotify lookup failed for track {spotify_track_id}")
            # Not a URL anymore - yt-dlp's default_search turns this into a
            # normal YouTube search, same as if the user had typed it directly.
            query = search_query

        loop = asyncio.get_running_loop()

        def extract():
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                info = ydl.extract_info(query, download=False)
                if info is not None and "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    if not entries:
                        raise NoResultsError(f"No results for {query!r}")
                    info = entries[0]
                if info is None:
                    raise NoResultsError(f"No results for {query!r}")
                return info

        info = await loop.run_in_executor(None, extract)
        return Track(
            title=info.get("title") or "Unknown title",
            webpage_url=info.get("webpage_url") or query,
            stream_url=info["url"],
            video_id=info.get("id"),
            duration=info.get("duration"),
            requester_id=requester_id,
        )

    async def _ensure_stream_url(self, track: Track) -> bool:
        """Lazily resolves a playlist-queued track's real playable stream
        right before it's needed. Returns False (rather than raising) on
        failure so the caller can just skip this one and move on."""
        if track.stream_url:
            return True
        query = track.resolve_query or track.webpage_url
        if not query:
            return False
        try:
            resolved = await self._resolve(query, track.requester_id)
        except (yt_dlp.utils.DownloadError, NoResultsError):
            return False
        track.title = resolved.title
        track.webpage_url = resolved.webpage_url
        track.stream_url = resolved.stream_url
        track.video_id = resolved.video_id
        track.duration = resolved.duration
        return True

    async def _resolve_youtube_playlist(self, url: str, requester_id: int) -> list[Track]:
        loop = asyncio.get_running_loop()

        def extract():
            with yt_dlp.YoutubeDL(PLAYLIST_YDL_OPTS) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await loop.run_in_executor(None, extract)
        except yt_dlp.utils.DownloadError:
            return []
        entries = [e for e in (info.get("entries") or []) if e] if info else []

        tracks = []
        for entry in entries[:PLAYLIST_MAX_TRACKS]:
            video_id = entry.get("id")
            webpage_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else entry.get("url")
            if not webpage_url:
                continue
            tracks.append(
                Track(
                    title=entry.get("title") or "Unknown title",
                    webpage_url=webpage_url,
                    requester_id=requester_id,
                    video_id=video_id,
                    duration=entry.get("duration"),
                    resolve_query=webpage_url,
                )
            )
        return tracks

    async def _spotify_playlist_tracks(self, playlist_id: str, requester_id: int) -> list[Track]:
        token = await self._spotify_access_token()
        if token is None or self.session is None:
            return []
        tracks: list[Track] = []
        url = f"{SPOTIFY_API_BASE}/playlists/{playlist_id}/tracks?limit=100"
        headers = {"Authorization": f"Bearer {token}"}
        while url and len(tracks) < PLAYLIST_MAX_TRACKS:
            try:
                async with self.session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 404:
                        break
                    resp.raise_for_status()
                    data = await resp.json()
            except Exception:
                logger.warning("Spotify playlist lookup failed for %s", playlist_id, exc_info=True)
                break
            for item in data.get("items", []):
                item_track = item.get("track")
                if not item_track:
                    continue
                artists = ", ".join(a["name"] for a in item_track.get("artists", []))
                title = item_track.get("name", "")
                search_query = f"{artists} - {title}".strip(" -")
                if not search_query:
                    continue
                spotify_url = item_track.get("external_urls", {}).get("spotify", search_query)
                tracks.append(
                    Track(
                        title=search_query,
                        webpage_url=spotify_url,
                        requester_id=requester_id,
                        resolve_query=search_query,
                    )
                )
            url = data.get("next")
        return tracks[:PLAYLIST_MAX_TRACKS]

    async def _prompt_playlist_choice(self, ctx: commands.Context) -> str:
        view = PlaylistChoiceView(ctx.author.id)
        prompt = await ctx.send(
            "That link is part of a playlist - just this song, or queue the whole playlist?",
            view=view,
        )
        await view.wait()
        try:
            await prompt.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        return view.choice or "single"

    async def _fetch_skip_segments(self, video_id: str) -> list[tuple[float, float]]:
        if self.session is None:
            return []
        try:
            async with self.session.get(
                SPONSORBLOCK_API,
                params={
                    "videoID": video_id,
                    "categories": str(SPONSORBLOCK_CATEGORIES).replace("'", '"'),
                },
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
                    return []
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            logger.warning("SponsorBlock lookup failed for video %s", video_id, exc_info=True)
            return []
        return merge_skip_segments([(seg["segment"][0], seg["segment"][1]) for seg in data])

    # ---- now-playing message / buttons ----

    def _now_playing_embed(self, track: Track) -> discord.Embed:
        embed = discord.Embed(
            title="Now playing",
            description=f"[{track.title}]({track.webpage_url})",
            color=discord.Color.blurple(),
        )
        return embed

    def _format_queue_listing(self, state: GuildMusicState) -> str:
        if state.current is None:
            return "Nothing is playing and the queue is empty."
        lines = [f"**Now playing:** {state.current.title}"]
        if state.queue:
            lines.append("**Up next:**")
            lines += [f"#{i} {track.title}" for i, track in enumerate(state.queue, start=1)]
        else:
            lines.append("*Queue is empty.*")
        return "\n".join(lines[:22])

    async def _post_now_playing(
        self, state: GuildMusicState, track: Track, ctx: Optional[commands.Context] = None
    ) -> None:
        """A new track starting replaces the old now-playing message outright
        (deleted, not just edited) - the buttons need to end up on whatever
        message is newest in the channel."""
        await self._delete_player_message(state)
        embed = self._now_playing_embed(track)
        view = MusicControlView(self, state.guild_id)
        if ctx is not None:
            message = await ctx.send(embed=embed, view=view)
        elif state.text_channel is not None:
            message = await state.text_channel.send(embed=embed, view=view)
        else:
            return
        state.player_message = message
        state.view = view
        state.last_requester_id = track.requester_id

    async def _delete_player_message(self, state: GuildMusicState) -> None:
        if state.view is not None:
            state.view.stop()
            state.view = None
        if state.player_message is not None:
            try:
                await state.player_message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            state.player_message = None

    async def _strip_player_buttons(self, state: GuildMusicState) -> None:
        """Session's over - leave the last now-playing message as a recap
        instead of deleting it, but it shouldn't keep working buttons."""
        if state.view is not None:
            state.view.stop()
            state.view = None
        if state.player_message is not None:
            try:
                await state.player_message.edit(view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            state.player_message = None

    # ---- playback ----

    def _make_source(self, track: Track, seek_seconds: float) -> discord.PCMVolumeTransformer:
        before_options = FFMPEG_BEFORE_OPTIONS
        if seek_seconds > 0:
            before_options = f"-ss {seek_seconds} {before_options}"
        source = discord.FFmpegPCMAudio(
            track.stream_url, before_options=before_options, options=FFMPEG_OPTIONS
        )
        return discord.PCMVolumeTransformer(source, volume=1.0)

    async def _start_playback(
        self,
        state: GuildMusicState,
        track: Track,
        seek_seconds: float = 0.0,
        announce: bool = True,
        ctx: Optional[commands.Context] = None,
    ) -> None:
        state.current = track
        state.seek_offset = seek_seconds
        state.play_started_at = time.monotonic()
        if seek_seconds == 0.0:
            state.skip_segments = []
            if state.sponsorblock_enabled and track.video_id:
                state.skip_segments = await self._fetch_skip_segments(track.video_id)
        gen = state.generation

        def after_play(error: Optional[Exception]):
            if error:
                logger.error("Playback error in guild %s: %s", state.guild_id, error)
            if gen != state.generation:
                return  # stale callback from a track we deliberately interrupted
            asyncio.run_coroutine_threadsafe(self._advance(state), self.bot.loop)

        source = self._make_source(track, seek_seconds)
        state.voice_client.play(source, after=after_play)

        if announce:
            await self._post_now_playing(state, track, ctx=ctx)

    async def _ensure_and_play(
        self, state: GuildMusicState, track: Track, ctx: Optional[commands.Context] = None
    ) -> bool:
        """Resolves a track's stream if it's a lazy playlist entry, then
        plays it. Returns False if it couldn't be resolved."""
        if not await self._ensure_stream_url(track):
            return False
        await self._start_playback(state, track, ctx=ctx)
        return True

    async def _advance(self, state: GuildMusicState) -> None:
        while state.queue:
            next_track = state.queue.popleft()
            if await self._ensure_and_play(state, next_track):
                return
            if state.text_channel is not None:
                try:
                    await state.text_channel.send(
                        f"Couldn't load **{next_track.title}**, skipping.",
                        delete_after=TRANSIENT_MESSAGE_SECONDS,
                    )
                except discord.HTTPException:
                    pass
        state.current = None
        state.play_started_at = None
        state.skip_segments = []
        await self._announce_queue_empty(state)
        self._schedule_idle_disconnect(state)

    async def _announce_queue_empty(self, state: GuildMusicState) -> None:
        if state.text_channel is None:
            return
        if state.ping_last_requester and state.last_requester_id:
            text = f"<@{state.last_requester_id}> the queue is empty."
        else:
            text = "The queue is empty."
        try:
            await state.text_channel.send(text)
        except discord.HTTPException:
            pass

    async def _seek_within_track(self, state: GuildMusicState, target_seconds: float) -> None:
        track = state.current
        if track is None or state.voice_client is None:
            return
        state.generation += 1
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            state.voice_client.stop()
        # A SponsorBlock seek is still the same track playing - no need to
        # repost the now-playing message/buttons for it.
        await self._start_playback(state, track, seek_seconds=target_seconds, announce=False)

    async def _full_stop(self, state: GuildMusicState) -> None:
        state.queue.clear()
        state.generation += 1
        if state.voice_client is not None:
            if state.voice_client.is_playing() or state.voice_client.is_paused():
                state.voice_client.stop()
            await state.voice_client.disconnect()
            state.voice_client = None
        state.current = None
        state.play_started_at = None
        state.skip_segments = []
        if state.idle_task and not state.idle_task.done():
            state.idle_task.cancel()
        await self._strip_player_buttons(state)

    def _schedule_idle_disconnect(self, state: GuildMusicState) -> None:
        if state.idle_task and not state.idle_task.done():
            state.idle_task.cancel()
        state.idle_task = self.bot.loop.create_task(self._idle_disconnect(state))

    async def _idle_disconnect(self, state: GuildMusicState) -> None:
        try:
            await asyncio.sleep(IDLE_DISCONNECT_SECONDS)
        except asyncio.CancelledError:
            return
        if state.current is None and not state.queue and state.voice_client is not None:
            await state.voice_client.disconnect()
            state.voice_client = None
            await self._strip_player_buttons(state)

    @tasks.loop(seconds=POSITION_CHECK_INTERVAL)
    async def position_check(self):
        for state in list(self.states.values()):
            if not state.skip_segments or state.voice_client is None:
                continue
            if not state.voice_client.is_playing():
                continue
            target = next_skip_target(state.position(), state.skip_segments)
            if target is not None:
                await self._seek_within_track(state, target)

    @position_check.before_loop
    async def before_position_check(self):
        await self.bot.wait_until_ready()

    # ---- guards ----

    async def _require_shared_voice(self, ctx: commands.Context) -> bool:
        state = self._state(ctx.guild.id)
        if state.voice_client is None:
            await ctx.send(
                "I'm not in a voice channel.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS
            )
            return False
        author_voice = getattr(ctx.author, "voice", None)
        if (
            author_voice is None
            or author_voice.channel is None
            or author_voice.channel.id != state.voice_client.channel.id
        ):
            await ctx.send(
                "You need to be in my voice channel to do that.",
                ephemeral=True,
                delete_after=TRANSIENT_MESSAGE_SECONDS,
            )
            return False
        return True

    # ---- commands ----

    @commands.hybrid_command(name="play", description="Play a song by search query or URL")
    @commands.guild_only()
    async def play(self, ctx: commands.Context, *, query: str):
        author_voice = getattr(ctx.author, "voice", None)
        if author_voice is None or author_voice.channel is None:
            await ctx.send(
                "Join a voice channel first.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS
            )
            return

        await ctx.defer()
        state = self._state(ctx.guild.id)
        state.text_channel = ctx.channel
        channel = author_voice.channel

        if state.voice_client is None or not state.voice_client.is_connected():
            state.voice_client = await channel.connect(self_deaf=True, self_mute=False)
        elif state.voice_client.channel.id != channel.id:
            await state.voice_client.move_to(channel)

        if state.idle_task and not state.idle_task.done():
            state.idle_task.cancel()

        state.sponsorblock_enabled = await self.bot.stores.config.get_bool(
            ctx.guild.id, "music.sponsorblock_enabled", True
        )

        spotify_playlist_id = extract_spotify_playlist_id(query)
        youtube_kind = classify_youtube_link(query)

        want_playlist = bool(spotify_playlist_id) or youtube_kind == "playlist"
        if not want_playlist and youtube_kind == "video_in_playlist":
            want_playlist = await self._prompt_playlist_choice(ctx) == "playlist"

        if want_playlist:
            await self._queue_playlist(ctx, state, query, spotify_playlist_id)
        else:
            await self._queue_single_track(ctx, state, query)

    async def _queue_single_track(self, ctx: commands.Context, state: GuildMusicState, query: str) -> None:
        try:
            track = await self._resolve(query, ctx.author.id)
        except SpotifyUnavailableError:
            await ctx.send(
                "Spotify links aren't set up on this bot yet (missing SPOTIFY_CLIENT_ID/"
                "SPOTIFY_CLIENT_SECRET) - try a direct YouTube/SoundCloud link or a plain search instead.",
                ephemeral=True,
                delete_after=TRANSIENT_MESSAGE_SECONDS,
            )
            return
        except (yt_dlp.utils.DownloadError, NoResultsError):
            await ctx.send(
                "Couldn't find anything to play for that - it might not exist, or it could be "
                "private, age-restricted, or region-locked.",
                ephemeral=True,
                delete_after=TRANSIENT_MESSAGE_SECONDS,
            )
            return

        if state.current is not None:
            state.queue.append(track)
            position = len(state.queue)
            await ctx.send(f"Queued: **{track.title}** (position #{position} in queue)")
        else:
            await self._start_playback(state, track, ctx=ctx)

    async def _queue_playlist(
        self,
        ctx: commands.Context,
        state: GuildMusicState,
        query: str,
        spotify_playlist_id: Optional[str],
    ) -> None:
        if spotify_playlist_id:
            if not os.getenv("SPOTIFY_CLIENT_ID") or not os.getenv("SPOTIFY_CLIENT_SECRET"):
                await ctx.send(
                    "Spotify links aren't set up on this bot yet (missing SPOTIFY_CLIENT_ID/"
                    "SPOTIFY_CLIENT_SECRET).",
                    ephemeral=True,
                    delete_after=TRANSIENT_MESSAGE_SECONDS,
                )
                return
            tracks = await self._spotify_playlist_tracks(spotify_playlist_id, ctx.author.id)
        else:
            tracks = await self._resolve_youtube_playlist(query, ctx.author.id)

        if not tracks:
            await ctx.send(
                "Couldn't load anything from that playlist.",
                ephemeral=True,
                delete_after=TRANSIENT_MESSAGE_SECONDS,
            )
            return

        capped_note = f" (capped at {PLAYLIST_MAX_TRACKS})" if len(tracks) >= PLAYLIST_MAX_TRACKS else ""

        if state.current is not None:
            state.queue.extend(tracks)
            await ctx.send(f"Queued {len(tracks)} tracks from the playlist{capped_note}.")
            return

        # All tracks go on the queue, then pop-and-try until one actually
        # resolves - same resilient pattern _advance uses, so a dead entry
        # at the start of the playlist doesn't block the rest of it. The
        # "Now playing" embed (posted separately via state.text_channel)
        # covers the visible confirmation, so this response is just a
        # lightweight, self-dismissing ack of the interaction.
        state.queue.extend(tracks)
        started = False
        while not started and state.queue:
            started = await self._ensure_and_play(state, state.queue.popleft())
        if started:
            await ctx.send(
                f"Loaded {len(tracks)} tracks from the playlist{capped_note}.",
                ephemeral=True,
                delete_after=TRANSIENT_MESSAGE_SECONDS,
            )
        else:
            await ctx.send(
                "Couldn't play anything from that playlist.",
                ephemeral=True,
                delete_after=TRANSIENT_MESSAGE_SECONDS,
            )

    @commands.hybrid_command(name="skip", description="Skip the current track")
    @commands.guild_only()
    async def skip(self, ctx: commands.Context):
        if not await self._require_shared_voice(ctx):
            return
        state = self._state(ctx.guild.id)
        if state.current is None:
            await ctx.send("Nothing is playing.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)
            return
        state.voice_client.stop()
        await ctx.send("Skipped.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)

    @commands.hybrid_command(name="rewind", description="Rewind 10 seconds")
    @commands.guild_only()
    async def rewind_cmd(self, ctx: commands.Context):
        if not await self._require_shared_voice(ctx):
            return
        state = self._state(ctx.guild.id)
        if state.current is None:
            await ctx.send("Nothing is playing.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)
            return
        target = clamp_seek_target(state.position(), -SEEK_STEP_SECONDS, state.current.duration)
        await self._seek_within_track(state, target)
        await ctx.send(f"Rewound to {int(target)}s.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)

    @commands.hybrid_command(name="forward", description="Skip forward 10 seconds")
    @commands.guild_only()
    async def forward_cmd(self, ctx: commands.Context):
        if not await self._require_shared_voice(ctx):
            return
        state = self._state(ctx.guild.id)
        if state.current is None:
            await ctx.send("Nothing is playing.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)
            return
        target = clamp_seek_target(state.position(), SEEK_STEP_SECONDS, state.current.duration)
        await self._seek_within_track(state, target)
        await ctx.send(f"Jumped to {int(target)}s.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)

    @commands.hybrid_command(name="pause", description="Pause playback")
    @commands.guild_only()
    async def pause(self, ctx: commands.Context):
        if not await self._require_shared_voice(ctx):
            return
        state = self._state(ctx.guild.id)
        if not state.voice_client.is_playing():
            await ctx.send("Nothing is playing.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)
            return
        state.voice_client.pause()
        await ctx.send("Paused.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)

    @commands.hybrid_command(name="resume", description="Resume playback")
    @commands.guild_only()
    async def resume(self, ctx: commands.Context):
        if not await self._require_shared_voice(ctx):
            return
        state = self._state(ctx.guild.id)
        if not state.voice_client.is_paused():
            await ctx.send("Nothing is paused.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)
            return
        state.voice_client.resume()
        await ctx.send("Resumed.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)

    @commands.hybrid_command(name="stop", description="Stop playback, clear the queue, and leave")
    @commands.guild_only()
    async def stop(self, ctx: commands.Context):
        if not await self._require_shared_voice(ctx):
            return
        state = self._state(ctx.guild.id)
        await self._full_stop(state)
        await ctx.send("Stopped and cleared the queue.")

    @commands.hybrid_command(name="leave", description="Leave the voice channel")
    @commands.guild_only()
    async def leave(self, ctx: commands.Context):
        if not await self._require_shared_voice(ctx):
            return
        state = self._state(ctx.guild.id)
        await self._full_stop(state)
        await ctx.send("Left the voice channel.")

    @commands.hybrid_command(name="queue", description="Show the current queue")
    @commands.guild_only()
    async def queue_cmd(self, ctx: commands.Context):
        state = self._state(ctx.guild.id)
        await ctx.send(self._format_queue_listing(state), ephemeral=True)

    @commands.hybrid_command(name="nowplaying", description="Show the currently playing track")
    @commands.guild_only()
    async def nowplaying(self, ctx: commands.Context):
        state = self._state(ctx.guild.id)
        if state.current is None:
            await ctx.send("Nothing is playing.", ephemeral=True, delete_after=TRANSIENT_MESSAGE_SECONDS)
            return
        embed = self._now_playing_embed(state.current)
        if state.current.duration:
            embed.add_field(
                name="Position", value=f"{int(state.position())}s / {int(state.current.duration)}s"
            )
        await ctx.send(embed=embed, ephemeral=True)

    # ---- auto-leave when abandoned ----

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot:
            return
        state = self.states.get(member.guild.id)
        if state is None or state.voice_client is None:
            return
        channel = state.voice_client.channel
        if before.channel != channel and after.channel != channel:
            return
        if not any(not m.bot for m in channel.members):
            await self._full_stop(state)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
