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
**Symptom:** Small but noticeable audio dropouts in AIOC path every ~5 seconds.
**Cause:** Sub-chunk pacing used absolute-from-now timing. Sleep overshoot accumulated.
**Fix:** Changed to incremental pacing with snap-forward. Queue maxsize 4→8.

## 2026-02-22 — SDR audio stuttering (750ms on / 750ms off)
**Symptom:** Regular pattern of ~750ms audio followed by ~750ms silence.
**Cause:** `deque(maxlen=4)` silently dropped oldest chunks when full.
**Fix:** Replaced with `bytearray` ring buffer + `threading.Lock`.

## 2026-02-24 — Status bar freezes after ~7-10 seconds
**Symptom:** Status bar stops updating; audio continues fine.
**Cause:** Mixer path never updated `last_audio_capture_time`. STOP branch triggered restart loop.
**Fix:** Added timestamp updates in mixer's else branch.

## 2026-02-24 — Speaker output drops from GIL contention
**Symptom:** Small audio drops correlated with status bar draws.
**Cause:** Blocking-write speaker thread needed GIL between writes.
**Fix:** Converted to PortAudio callback mode with internal buffering.

## 2026-02-24 — Config parser didn't strip quotes from string values
**Symptom:** `SPEAKER_OUTPUT_DEVICE = "c-media"` not matching device name.
**Fix:** Added quote stripping in `load_config()`.

## 2026-02-24 — SP bar graph frozen when RX muted (R key)
**Fix:** Added `speaker_audio_level`, updated in `_speaker_enqueue()`.

## 2026-02-24 — Duck-OUT transition silencing Radio for 1s
**Fix:** Removed `mixed_audio = None` during duck-OUT transitions.

## 2026-02-24 — SDR breakthrough during AIOC inter-blob gaps
**Fix:** Increased hold timer from 500ms to 1000ms.

## 2026-02-24 — SDR bleeding at start of Radio transmission
**Fix:** Reduced `SIGNAL_ATTACK_TIME` from 0.5 to 0.15.

## 2026-02-24 — Source loop restructure for real-time SDR handling
**Fix:** Split source loop into Phase 1 (non-SDR) → duck state → Phase 2 (SDR).

## 2026-02-24 — FilePlayback fixes (load stall, transitions, clicks, cache)
- Preload worker thread eliminates initial decode stall
- Staging buffer for instant clip transitions (no I/O on mixer thread)
- Startup audio cache pre-decodes all 10 slots at init
- 5ms fade-in/fade-out via `_apply_boundary_fade()`
- PTT leak fix: check ptt_active in get_audio()

## 2026-02-25 — AIOC 400-450ms silence gaps at start of reception
**Symptom:** Trace showed 2 gaps of 400-450ms during active radio reception.
AIOC blobs arriving every ~850ms instead of ~400ms at start, then catching up.
**Cause:** With 8× ALSA period (400ms blobs), no delivery cushion. If a blob
arrives even 50ms late, sub-buffer is already empty → full 400ms silence gap.
**Fix:** Reduced period from 8× to 4× (200ms blobs). Added pre-buffer gate:
accumulate 2 blobs (400ms) before first serve. Queue maxsize 8→16.
get_audio() now eagerly drains ALL queued blobs every tick (critical fix — old
loop only fetched when sub_buffer < chunk_bytes, which starved pre-buffer).
**IMPORTANT:** First attempt caused "total disaster" (no audio at all) because
the while loop condition `< cb` prevented fetching enough for the pre-buffer
threshold. Fixed by replacing with unconditional eager drain loop.

## 2026-02-25 — SDR2 ducked by SDR1 when SDR1 source sends silence
**Symptom:** SDR2 remains ducked whenever SDR1 is connected, even if SDR1's
source is sending silence. Only manually muting SDR1 (key 's') lets SDR2 play.
**Cause:** Rule 2 SDR-vs-SDR ducking checked if the higher-priority SDR was in
`sdrs_to_include`, but SDR1 gets included when `sdr_is_sole_source` is True
(no radio/PTT active) regardless of actual signal. The code never verified that
SDR1 had actual audio before ducking SDR2.
**Fix:** Added `_sdr_trace` lookup in the Rule 2 loop: only duck if the
higher-priority SDR has `sig` (instant signal) or `hold` (recent signal hold).
SDR1 included only because it's the sole source type no longer ducks SDR2.

## 2026-02-25 — Speaker drops when terminal loses desktop focus
**Symptom:** ~10 micro-drops per second in speaker output when terminal window
not focused. Audio perfect when focused. Mumble path unaffected.
**Cause:** Linux CFS scheduler deprioritizes background processes. The Python
audio transmit loop thread gets less CPU time, can't maintain 50ms tick rate,
speaker callback starves.
**Fix:** `audio_transmit_loop()` sets `SCHED_RR` (realtime round-robin, priority 10)
on its own thread at startup. Falls back to `nice -10` if no root. Only this
thread is elevated — status bar, keyboard handler etc. stay at normal priority.
