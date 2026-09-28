# Local radio surveys

Regional Markdown surveys contain observations from an actual recording, with
frequencies in MHz, evidence for modulation and protocol identification, and
OpenWebRX+ receiver/decoder settings to try. Start with [San Diego](SanDiego/).

Learning guides may also contain researched listening candidates. They distinguish
verified reception from documented transmitter sites, regional channel plans,
distant or scheduled signals, and modes without a verified local example.

A survey is a snapshot of reception at one antenna during one observation period.
It is not a complete frequency directory: silent transmitters, weak signals,
overlapping transmissions, receiver artifacts, and tuning errors limit detection.
A database allocation alone is not evidence that a transmitter was received.

## Evidence labels

- **Confirmed:** repeated protocol evidence with corroborating decoder output;
  identify which checks the decoder actually performs. A single sync match or
  an unvalidated header is insufficient.
- **Probable:** measured modulation features support the interpretation, but
  protocol identification has not been confirmed.
- **Try:** receiver/decoder suggestions for an unresolved observed signal.
- **Artifact candidate:** a spectral feature that may originate in the receiver
  or local electronics; do not equate it with an on-air station.

Each survey records the observation interval, receiver, frequency coverage,
sample rate, gain, tuning correction, detection method, decoder trials, and
limitations. Use dated filenames so later observations supplement earlier ones.

Raw IQ, demodulated audio, and decoded message contents stay outside Git. Reports
summarize signal characteristics and protocol evidence. Small plots and tables
may accompany the Markdown. Local captures are currently kept in the ignored
`.build-linux/region-scan-453/` and `.build-linux/learning-guide/` directories; that build directory can be removed by
build-cleaning commands, so copy captures elsewhere if permanent retention is
needed.
