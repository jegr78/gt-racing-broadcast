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

## Two AI features (epic #1023, issue #1028)

The component shows the AI Race Engineer and the AI Coach as two labelled sections
in the driver desk and the Director Solo card, and the Control Center form as three
cards: AI Race Engineer (switch and agent), AI Coach (switch and agent) and the
shared Voice. Messages are split by their `source`; a `refusal` (the fixed
`feature_disabled` hint of a switched-off feature, no engineer data) is labelled
"Feature off" and renders in the shared Voice row while it is the newest message of
the session, since its own section only says Off. Each section handles its own switch: a 403 `feature_disabled` from
the engineer routes empties and hides only the engineer section, a 403
`feature_disabled` from the coach route (or `whoami.ai_coach` false) does the same
for the coach section, and both show "Off · enable it in the Control Center". A 401
or any other 403 still denies the whole view. The relay serves
`messages` and `native` while either feature is on (403 `feature_disabled` naming
both only when both are off) and filters `messages` to the sources whose feature
is on, letting `refusal` messages through (the HUD never shows them); `snapshot`, `setup`, `service` and `request` stay engineer-only. The view
polls the shared pair and the engineer pair independently, so a coach-only view
keeps its coach messages and the shared Voice row (pause, mute, volume, input
language), which sits above both sections. A 403 `feature_disabled` on a `native`
write (both features off) hides the Voice row as well as the engineer section.
The single coach owner per profile removed the "busy, another driver" phase and the
`training_busy` handling from the coach view. Tests: `t_admin_form_saves_two_features`,
`t_sections_split_messages_by_source`,
`t_a_refusal_shows_in_the_voice_row_where_the_driver_looks`,
`t_a_native_refusal_with_both_features_off_hides_the_voice_row`,
`t_a_disabled_feature_turns_off_only_its_own_section`,
`t_coach_only_view_keeps_the_voice_controls_and_coach_messages` (node, fake DOM),
`t_messages_and_native_serve_either_feature_and_only_enabled_sources` (signed HTTP)
plus the two Playwright checks.
