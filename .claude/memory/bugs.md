# Bug History

## 2026-02-21 — AIOC USB timing jitter causing audio dropouts
**Symptom:** Audible dropout glitches in radio RX audio every few seconds.
**Cause:** CM108 chipset on Linux xHCI produces irregular USB frame delivery.
With a 50ms ALSA period (AUDIO_CHUNK_SIZE=2400), jitter accumulates to ~1700ms
of drift per 47s. arecord with `--buffer-size=19200` had zero xruns — confirming
the issue is at the PortAudio/PyAudio layer, not the AIOC hardware itself.
**Fix:** Switch input stream to PortAudio callback mode with
`frames_per_buffer = AUDIO_CHUNK_SIZE * 8` (19200 frames = 400ms period).
`AIOCRadioSource._audio_callback` stores each 400ms blob in `_chunk_queue`.
`get_audio()` slices it into 50ms sub-chunks with `time.sleep()`-based pacing
so the main loop still runs at the right rate. Tested: -0.1ms drift over 48s.

## 2026-02-21 — PTT click/pop on manual toggle
**Symptom:** Audible click in Mumble/speaker when pressing 'p' to toggle PTT.
**Cause:** The AIOC HID write that keys the radio relay causes a brief transient
on the USB isochronous audio path. The one-shot `_ptt_just_changed` flag was
cleared before the blob containing the transient arrived from the sub-buffer.
**Fix:** Replaced `_ptt_just_changed` bool with `_ptt_change_time` monotonic
timestamp. `AIOCRadioSource.get_audio()` applies a continuous gain envelope
(0 for 30ms, linear ramp 0→1 from 30→130ms) to all sub-chunks within 130ms
of any PTT state change. The keyboard handler now queues the HID write via
`_pending_ptt_state` so it runs between audio reads (no USB bus contention).

## 2026-02-21 — Speaker/Mumble desync after first PTT event
**Symptom:** Speaker and Mumble audio start in sync but drift ~100ms apart
permanently after the first PTT key-up or key-down event.
**Cause:** Speaker output bypasses VAD (always receives audio), but Mumble is
VAD-gated. During the 130ms click-suppression window (which applies silence/fade),
VAD reports no signal so Mumble skips ~100ms of chunks that the speaker plays.
**Fix:** Force `should_transmit = True` in `AIOCRadioSource.get_audio()` for the
entire 130ms window so Mumble receives the muted audio and stays in lock-step
with the speaker.

## 2026-02-21 — No click suppression when announcement activates PTT
**Symptom:** AIOC click audible on Mumble when an announcement file auto-keys PTT,
even though manual PTT toggle was click-free.
**Cause:** `_ptt_change_time` was only set in three places (Mumble RX handler,
pending-PTT apply, status_monitor release). The announcement code path called
`set_ptt_state(True)` directly without updating `_ptt_change_time`.
**Fix:** Added `self._ptt_change_time = time.monotonic()` immediately after the
`set_ptt_state(True)` call in the announcement PTT activation block.

## 2026-02-21 — `announcement_delay_active` stuck True after stop_playback
**Symptom:** After stopping playback mid-announcement, the next queued file would
silently skip audio while returning `ptt_required=True`, keeping PTT active
indefinitely if the delay timer had not yet expired.
**Cause:** `announcement_delay_active` is cleared inside the PTT branch of
`audio_transmit_loop`. When `stop_playback()` terminates the file, `ptt_required`
becomes False and the loop exits the PTT branch — so the expiry check never ran.
**Fix:** Added a safety clear before `get_mixed_audio()` on every loop iteration:
if `announcement_delay_active` and the timer has expired, clear the flag.
Also initialized both `announcement_delay_active` and `_announcement_ptt_delay_until`
in `__init__` (previously used `getattr(..., False)` defensive guards).

## 2026-02-21 — AIOC detection steals C-Media speaker device
**Symptom:** `Warning: Speaker output failed to open: [Errno -9985] Device unavailable`
**Cause:** `find_aioc_audio_device()` keyword list included `'c-media'`, which matched
the C-Media USB Headphone Set before the real AIOC. The headset was then opened as
both input and output AIOC streams, leaving it unavailable for speaker monitoring.
**Fix:** Replaced `'c-media'` with `'all-in-one'` in the keyword list.

## 2026-02-22 — Complete silence to Mumble (AUDIO_CHUNK_SIZE=9600)
**Symptom:** No radio audio reaches Mumble at all.
**Cause:** `AUDIO_CHUNK_SIZE` was changed from 2400 to 9600 in config. With 8× multiplier,
ALSA period became 76800 frames (1.6s). The queue timeout was hardcoded to 0.800s,
which always expired before the first callback blob arrived.
**Fix:** Restored `AUDIO_CHUNK_SIZE = 2400`. Made queue timeout dynamic:
`timeout = (AUDIO_CHUNK_SIZE * 8 / AUDIO_RATE) * 2`.

## 2026-02-22 — VAD units mismatch causes choppy audio
**Symptom:** VAD opens/closes too aggressively — no smooth envelope.
**Cause:** Config values in seconds divided by 1000.0 (treating as ms).
**Fix:** Removed `/1000.0` from attack/release coefficients; removed `*1000` from
duration calculations.

## 2026-02-22 — SDR buffer_multiplier computed but never used
**Symptom:** SDR_BUFFER_MULTIPLIER config had no effect.
**Cause:** `buffer_size = AUDIO_CHUNK_SIZE * buffer_multiplier` computed but
`frames_per_buffer=self.config.AUDIO_CHUNK_SIZE` used instead.
**Fix:** Changed `frames_per_buffer` to use `buffer_size`.

## 2026-02-22 — Duplicate SDR_BUFFER_MULTIPLIER config key
**Symptom:** SDR1's buffer multiplier setting ignored.
**Cause:** Key appeared twice in examples config; second overwrote first.
**Fix:** Removed duplicate entry.

## 2026-02-22 — AIOC periodic drops every ~5 seconds
**Symptom:** Small but noticeable audio dropouts in AIOC path every ~5 seconds,
heard in both Mumble and speaker output.
**Cause:** Sub-chunk pacing used `_next_delivery = time.monotonic() + chunk_secs`
(absolute-from-now). Sleep overshoot (~1ms per chunk × 8 chunks/blob = ~8ms/blob)
accumulated, causing consumption to slowly lag production and eventually drain
the queue momentarily.
**Fix:** Changed to incremental pacing: `_next_delivery += chunk_secs`. Added
snap-forward: if `now - _next_delivery > chunk_secs`, snap to now to prevent
catch-up bursts after a stall. Also increased queue maxsize 4→8.

## 2026-02-22 — SDR audio stuttering (750ms on / 750ms off)
**Symptom:** Regular pattern of ~750ms audio followed by ~750ms silence, repeating.
**Cause:** `deque(maxlen=4)` silently dropped oldest chunks when appending to a
full deque. The main loop ran slightly slower than 50ms per iteration (AIOC sleep
overshoot), causing it to fall behind. Burst of 4 chunks from one ALSA period
arrived before all 4 prior chunks were consumed; deque dropped old ones silently.
**Fix:** Replaced `deque(maxlen=4)` with a `bytearray` ring buffer + `threading.Lock`.
Reader thread reads full ALSA periods (`AUDIO_CHUNK_SIZE * SDR_BUFFER_MULTIPLIER`
frames per read). Ring buffer caps at 2 seconds; trims oldest data if exceeded.
`get_audio()` slices exactly `chunk_size * channels * 2` bytes from front.

## 2026-02-24 — Status bar freezes after ~7-10 seconds
**Symptom:** Status bar stops updating ~7-10s after gateway starts; audio continues
fine. Gateway still responds to Ctrl-C.
**Cause:** Mixer path in `audio_transmit_loop` never updated `last_audio_capture_time`
or `audio_capture_active`. These flags were only set inside `AIOCRadioSource.get_audio()`
(line 400). When AIOC returned None (no radio signal), the timestamp stayed at 0 or
went stale. After 10 seconds, `status_monitor_loop` entered the STOP branch which
called `restart_audio_input()` + `continue`, permanently skipping the status bar print.
**Fix:** Added `self.last_audio_capture_time = time.time()` and
`self.audio_capture_active = True` in the mixer's `else` branch (when data is not None).
Also hardened the status_monitor exception handler: `BaseException` instead of
`Exception`, with nested try/except on the trace event append.

## 2026-02-24 — Speaker output drops from GIL contention
**Symptom:** Small audio drops in speaker output, correlated with status bar draws.
When status bar froze, audio became perfect.
**Cause:** Speaker used a blocking-write thread (`_speaker_output_thread`) that needed
GIL between writes for `queue.get()`. Status bar's `print(flush=True)` held GIL for
5-10ms during string construction, causing speaker hardware buffer underruns.
**Fix:** Converted speaker output from blocking-write thread to PortAudio callback
mode (`_speaker_callback`). PortAudio callbacks run on a C thread with internal
buffering (2-3 periods), absorbing GIL delays. Also added clock drift drain logic
in `_speaker_enqueue()` (threshold 6→drain to 2) to absorb sw/hw clock drift.

## 2026-02-24 — Config parser didn't strip quotes from string values
**Symptom:** `SPEAKER_OUTPUT_DEVICE = "c-media"` not matching device name.
**Cause:** Config parser included surrounding quotes in the value string, so
case-insensitive match against `C-Media USB Headphone Set` failed.
**Fix:** Added quote stripping in `load_config()` before comment stripping and type
conversion.

## 2026-02-22 — SP bar graph frozen when RX muted (R key)
**Symptom:** SP:[bar] in status display stops updating (freezes) when radio RX
is muted with the R key.
**Cause:** SP bar displayed `tx_audio_level`, which is only updated inside
`AIOCRadioSource.get_audio()`. When `rx_muted=True`, get_audio() returns early
at line 401 before the level calculation, so `tx_audio_level` never updates.
**Fix:** Added `speaker_audio_level` attribute, updated in `_speaker_enqueue()`
from the actual audio sent to the speaker. SP bar now uses `speaker_audio_level`.
When muted, silence is enqueued so the level naturally decays to 0.

## 2026-02-24 — Status bar freezes after ~7-10 seconds
**Symptom:** Status bar stops updating ~7-10s after gateway starts; audio continues
fine. Gateway still responds to Ctrl-C.
**Cause:** Mixer path in `audio_transmit_loop` never updated `last_audio_capture_time`
or `audio_capture_active`. These flags were only set inside `AIOCRadioSource.get_audio()`
(line 400). When AIOC returned None (no radio signal), the timestamp stayed at 0 or
went stale. After 10 seconds, `status_monitor_loop` entered the STOP branch which
called `restart_audio_input()` + `continue`, permanently skipping the status bar print.
**Fix:** Added `self.last_audio_capture_time = time.time()` and
`self.audio_capture_active = True` in the mixer's `else` branch (when data is not None).
Also hardened the status_monitor exception handler: `BaseException` instead of
`Exception`, with nested try/except on the trace event append.

## 2026-02-24 — Speaker output drops from GIL contention
**Symptom:** Small audio drops in speaker output, correlated with status bar draws.
When status bar froze, audio became perfect.
**Cause:** Speaker used a blocking-write thread (`_speaker_output_thread`) that needed
GIL between writes for `queue.get()`. Status bar's `print(flush=True)` held GIL for
5-10ms during string construction, causing speaker hardware buffer underruns.
**Fix:** Converted speaker output from blocking-write thread to PortAudio callback
mode (`_speaker_callback`). PortAudio callbacks run on a C thread with internal
buffering (2-3 periods), absorbing GIL delays. Also added clock drift drain logic
in `_speaker_enqueue()` (threshold 6→drain to 2) to absorb sw/hw clock drift.

## 2026-02-24 — Config parser didn't strip quotes from string values
**Symptom:** `SPEAKER_OUTPUT_DEVICE = "c-media"` not matching device name.
**Cause:** Config parser included surrounding quotes in the value string, so
case-insensitive match against `C-Media USB Headphone Set` failed.
**Fix:** Added quote stripping in `load_config()` before comment stripping and type
conversion.

## 2026-02-24 — Mixer drops SDR audio when below -50dB signal threshold
**Symptom:** 21.1% silence gaps (up to 850ms) in audio stream when AIOC has no data
and SDR is the only active source.
**Cause:** `_mix_simultaneous()` uses `check_signal_instant()` with -50dB threshold
to decide whether to include SDR audio. When AIOC is between blob deliveries (no
radio RX), SDR is the only source but its audio near -50dB gets excluded by the gate.
`non_ptt_audio` stays None → `mixed_audio` stays None → silence sent to Mumble.
**Fix:** Added `sdr_is_sole_source = non_ptt_audio is None and ptt_audio is None`
check. When SDR is the only source type, always include it regardless of signal level.
Signal gating only matters when mixing with higher-priority sources to avoid adding
SDR noise.

## 2026-02-24 — Duck-OUT transition silences Radio audio for 1s
**Symptom:** ~3.1s of silence gaps (62 ticks) every time Radio returns after SDR
was playing. Trace showed `D-PTARO D D` state with NONE output.
**Cause:** `_mix_simultaneous()` set `mixed_audio = None` during duck-OUT transition
padding. SDRs were already silenced by `aioc_ducks_sdrs`, so this additionally
threw away the Radio audio for the entire SWITCH_PADDING_TIME (1s).
**Fix:** Removed the `mixed_audio = None` line during duck-OUT transitions.

## 2026-02-24 — SDR breakthrough during AIOC inter-blob gaps
**Symptom:** SDR plays for 300-350ms during active radio reception when AIOC has
a blob delivery gap exceeding the hold timer.
**Cause:** Hold timer (500ms) was shorter than AIOC blob gaps (up to 850ms).
When the hold expired mid-gap, the duck released, SDR played briefly, then the
next AIOC blob re-engaged the duck.
**Fix:** Increased hold timer from 500ms to 1000ms (covers 2× blob period).

## 2026-02-24 — SDR bleeding at start of Radio transmission
**Symptom:** SDR audible for ~500ms at the beginning of each Radio transmission.
**Cause:** `SIGNAL_ATTACK_TIME = 0.5` required 500ms of continuous above-threshold
audio before `has_actual_audio("Radio")` returned True. During those 500ms, SDRs
were not ducked and both played simultaneously.
**Fix:** Reduced `SIGNAL_ATTACK_TIME` from 0.5 to 0.15 (150ms, ~3 audio chunks).
Also increased `SIGNAL_RELEASE_TIME` from 2.0 to 3.0 for longer inter-transmission
hold (total 4.0s with 1.0s internal hold timer).

## 2026-02-24 — Source loop restructure for real-time SDR handling
**Symptom:** After extended AIOC ducking, SDR had no data when duck released (ring
buffer was drained during ducking). Also, ring buffer accumulated stale audio.
**Cause:** Original single source loop called `get_audio()` for ALL sources before
computing duck state. Ducked SDR audio was consumed and discarded.
**Fix:** Split source loop: Phase 1 collects non-SDR audio, duck state computed,
Phase 2 fetches SDR audio. Always call `get_audio()` (real-time: discard stale
ducked audio, don't accumulate it). SDR starts fresh when duck releases.
