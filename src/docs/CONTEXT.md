# Broadcast and telemetry analysis

Racecast supports producing a race broadcast and analysing recorded GT7 sessions. These activities can concern different league profiles at the same time.

## Language

**Broadcast profile**:
The league profile currently selected for broadcast production and its live services.
_Avoid_: Analysis profile, selected archive

**Analysis profile**:
The league profile whose recorded sessions, context and reports are being examined in a Telemetry workspace.
_Avoid_: Active broadcast profile

**Reference lap A**:
The explicitly selected benchmark lap in a comparison.
_Avoid_: Analysed lap B

**Analysed lap B**:
The lap being investigated against the reference, or by itself when no usable reference exists.
_Avoid_: Reference lap A

**Shared telemetry reference data**:
Track definitions and external vehicle references applicable across league profiles on the same producer's machine.
_Avoid_: Recording context, profile setup

**Recording context**:
The race settings, session annotations, setup evidence and notes associated with a particular recording.
_Avoid_: Shared telemetry reference data
