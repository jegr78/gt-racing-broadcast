# Synthetic voice fixtures

These WAV files contain eSpeak NG 1.52.0 speech, resampled by FFmpeg to mono
16 kHz signed 16-bit PCM. They contain no microphone capture or personal speech.
The filenames identify the prepared wake phrases, a negative example and English
or German continuous wake-plus-question speech. Automated ordinary tests use fake
recognizers; explicit model provisioning validates prepared phrases with these
fixtures before exposing them for selection. This validates model compatibility,
not real-world accuracy or acoustic safety.
