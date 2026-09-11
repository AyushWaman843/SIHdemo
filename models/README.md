# Local AASIST model

`aasist.onnx` is the official AASIST variant published by Speech Anti-Spoofing Benchmarks:

- Source: https://huggingface.co/SpeechAntiSpoofingBenchmarks/AASIST
- Architecture: official `clovaai/aasist` ASVspoof2019 LA checkpoint
- License: MIT
- Input: float32 mono waveform, 16 kHz, exactly 64,600 samples
- Output: two logits, index 0 is bona fide and index 1 is spoof
- SHA-256: `130e536266b7c537f9a13029e1612a9f392fd1cc827783683b6d1c062a3db5e1`

The application repeat-pads short clips and takes the first 64,600 samples of long clips, matching the model's published evaluation preprocessing. CYPHER additionally applies peak-safe RMS level normalization before inference because the model is highly sensitive to microphone gain. This normalization is application-side hardening, not part of the model's published benchmark preprocessing.
