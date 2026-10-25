# Driver radio browser controls

Epic #969, delivery #974. One shared component drives the Solo POV Director card
and the dedicated authenticated driver desk. It never captures or plays audio;
the deliberately enabled native relay service owns those lifetimes.

| Requirement | Production path | Positive test | Failure test | Runtime evidence |
| --- | --- | --- | --- | --- |
| Four shared presets and current messages | shared browser component and existing signed routes | both views read the same message ID | denied, stale, changed binding, failed request | production component in a real browser |
| Compact Solo POV card | existing Director solo layout | visible for Solo POV, controls still usable | other templates and direct-panel sign-in | synthetic Director screenshots |
| Bounded desk history and evidence | shared history/evidence renderer | measured fuel, pace, tyres and questions | empty/expired history and unavailable evidence | browser assertions and desktop/phone screenshots |
| Optional setup and confirmed service | revisioned forms | laps/time/reserve/compound and tyre confirmation | conflicting editor or obsolete session | signed HTTP and concurrent browser cases |
| Native pause, language, volume/mute | current native controls | status/control reflects other views | access loss, failed synthesis, worker failure | fake native runtime through production UI |
| Producer configuration | local Control Center proxy | explicit opt-in, driver assignment, microphone, providers | foreign origin, wrong profile, unavailable relay | real local HTTP and synthetic Control Center screenshot |
| Safe embedding and revocation | Console identity and existing grants | signed driver-only desk and direct panel cookie | Director/Engineer role alone, revoked role or profile | signed HTTP and no retained private DOM after denial |
| Native lifetime independent of views | no browser audio or capture | opening/closing views leaves native service running | reconnect cannot play old messages | component teardown and shared native tests |

Static shared JavaScript is available under the Console mount for both views;
it contains no session data. Every data and control operation remains signed and
profile-authorized. A direct panel can display its sign-in state without changing
existing production controls. UI text and spoken answers remain English.

Setup drafts survive preset/native-control actions and temporary connection
failures. A save acknowledgement advances the form's base revision while keeping
edits made during that save. Explicit reload, session/profile change or a current
access denial clears the draft. A view generation discards old successful replies
after denial or context change, so an in-flight authorized poll cannot restore
revoked data. Prior validated interpretations remain visible with their messages.
