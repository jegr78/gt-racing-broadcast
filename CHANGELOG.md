# Changelog

## [1.10.1](https://github.com/jegr78/gt-racing-broadcast/compare/v1.10.0...v1.10.1) (2026-09-10)


### Bug Fixes

* **cli:** open the browser without the frozen library path ([#579](https://github.com/jegr78/gt-racing-broadcast/issues/579)) ([f9aa243](https://github.com/jegr78/gt-racing-broadcast/commit/f9aa2434334e364290f66c5750eca80cd374ee80))

## [1.10.0](https://github.com/jegr78/gt-racing-broadcast/compare/v1.9.0...v1.10.0) (2026-08-28)


### Features

* **smoketest:** verify the event core after a toolchain update ([#571](https://github.com/jegr78/gt-racing-broadcast/issues/571)) ([d6a3874](https://github.com/jegr78/gt-racing-broadcast/commit/d6a38747b7b33c58d6712fcbb00536a83cb35dd9))
* **tools:** render league broadcast assets from a profile asset kit ([#562](https://github.com/jegr78/gt-racing-broadcast/issues/562)) ([774e2f3](https://github.com/jegr78/gt-racing-broadcast/commit/774e2f3b1983355f8de046594171dfb50ed8c097))


### Bug Fixes

* **event:** launch OBS and Discord without the frozen library path ([#573](https://github.com/jegr78/gt-racing-broadcast/issues/573)) ([45766d6](https://github.com/jegr78/gt-racing-broadcast/commit/45766d65ea416b49555a21fc95db126ff97fb2ac))
* **hooks:** only treat a real merge as a merge ([#566](https://github.com/jegr78/gt-racing-broadcast/issues/566)) ([05dc572](https://github.com/jegr78/gt-racing-broadcast/commit/05dc5729e9ee199dc8511aa7d4e3568e68f8e03f))
* **relay:** make the program-audio join decodable on an fMP4 feed ([#578](https://github.com/jegr78/gt-racing-broadcast/issues/578)) ([7450224](https://github.com/jegr78/gt-racing-broadcast/commit/7450224e4d194854b7373bbe339a514ed0e944db)), closes [#576](https://github.com/jegr78/gt-racing-broadcast/issues/576)
* **smoketest:** confirm the sheet transition per row, not the webhook's HTTP result ([#574](https://github.com/jegr78/gt-racing-broadcast/issues/574)) ([490ec2d](https://github.com/jegr78/gt-racing-broadcast/commit/490ec2d71a9010f0ad7cd7d2af72eb08dee71553))
* **ui:** keep serving after the OS reaps the frozen build's temp dir ([#564](https://github.com/jegr78/gt-racing-broadcast/issues/564)) ([9a30f54](https://github.com/jegr78/gt-racing-broadcast/commit/9a30f54450bba51e75390d1b272f75628d6f2708))

## [1.9.0](https://github.com/jegr78/gt-racing-broadcast/compare/v1.8.0...v1.9.0) (2026-08-03)


### Features

* Arch Linux (pacman) support for install-tools and install-apps ([5080d2a](https://github.com/jegr78/gt-racing-broadcast/commit/5080d2aa6c46ecf9e17ac9d7c13ef208451ac802))
* **hud:** brand tile colours, qualifying best lap, relay mode ([#555](https://github.com/jegr78/gt-racing-broadcast/issues/555)) ([#557](https://github.com/jegr78/gt-racing-broadcast/issues/557)) ([c6c9446](https://github.com/jegr78/gt-racing-broadcast/commit/c6c9446bdaed6570afbbbc927917106070f35d2b))
* **install-apps:** install the apps with pacman, and stop re-installing plugins ([f0a5399](https://github.com/jegr78/gt-racing-broadcast/commit/f0a53993a5da1e622ff279565b5d303d6839c980))
* **install-tools:** install the tool chain with pacman on Arch ([2cca61d](https://github.com/jegr78/gt-racing-broadcast/commit/2cca61d75f6055e86d6c209cf74f7c944b9b6df8))
* solo mode, GT7 telemetry + GT Racing rebrand (merge epic [#300](https://github.com/jegr78/gt-racing-broadcast/issues/300)) ([d0c8713](https://github.com/jegr78/gt-racing-broadcast/commit/d0c8713f28ea45512102d1621e6334d5e305cff5))


### Bug Fixes

* **codeql:** close probe socket + drop redundant test import ([#552](https://github.com/jegr78/gt-racing-broadcast/issues/552)) ([e098b52](https://github.com/jegr78/gt-racing-broadcast/commit/e098b5203324e9ce464ae9d76a7b85910c4a993f))
* **event:** hand the login session's runtime dir to GUI apps started over SSH ([#560](https://github.com/jegr78/gt-racing-broadcast/issues/560)) ([9809312](https://github.com/jegr78/gt-racing-broadcast/commit/9809312743d034fe004165f7c22392b1cfda1b91))
* **media:** force-overwrite clip downloads so a stale/placeholder file refreshes ([#553](https://github.com/jegr78/gt-racing-broadcast/issues/553)) ([ad243df](https://github.com/jegr78/gt-racing-broadcast/commit/ad243df4f0a0306a7ea53fb04e23d7ed67495e72))
* **obs-browser:** send Arch hosts to the package, not a CEF source build ([c402e89](https://github.com/jegr78/gt-racing-broadcast/commit/c402e89cd20cc6de1aaf80546b8fdee8be169f3d))
* **obs:** hide Discord outside the Interview scene in the endurance collection ([#559](https://github.com/jegr78/gt-racing-broadcast/issues/559)) ([588550d](https://github.com/jegr78/gt-racing-broadcast/commit/588550d8881903368818ddf8430eb2179661dc2f))

## [1.8.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.7.1...v1.8.0) (2026-07-19)


### Features

* **companion:** reorganise button board — 3 topic pages, colour-coded, fixed labels ([#549](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/549)) ([70594a0](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/70594a086d380a04a36c97a39f7775c5e1e58a0a))
* inbound feed-stall signal — quiet-yellow health for source jitter ([#535](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/535)) ([#543](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/543)) ([b328ec0](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/b328ec03331bafb0ef538e95f3f1c4a63f09fea2))
* persistent obs-websocket connections — kill the connect-per-poll churn ([#537](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/537)) ([#544](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/544)) ([4a0824a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/4a0824a92edd4f7379e344139508a98db55afce3))
* **relay:** trailing-cursor prebuffer to eliminate source-jitter stutters ([#533](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/533)) ([#539](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/539)) ([4838245](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/483824512ef73677d0bdbe430a566c31d16835be))
* **report:** show host CPU/RAM/network in the post-event report ([#536](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/536)) ([#542](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/542)) ([bb89b7b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/bb89b7bf5f3bf3622b66d7b1789c3c8e49ee378d))
* Trailer broadcast video (Assets-managed, OBS scene, Panel + Web Buttons) ([#547](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/547)) ([c75875f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c75875f54fdbac64e49662817aebd0df4aa99b63))


### Bug Fixes

* **console:** accept Companion v5 backtick version marker for Web Buttons ([#546](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/546)) ([c3d30c5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c3d30c508a8148a3fef7c42b3608960ad963a2b4))
* **discord:** surface the real Discord auth error (invalid_scope) instead of a misleading 400 ([#531](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/531)) ([c70261e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c70261e7de18f7373c1406e182399fc3aaf9e61c))
* **obs-ws:** clear CodeQL alerts from the persistent-connection change ([#537](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/537)) ([#545](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/545)) ([862cbd2](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/862cbd2f7628efd391d1e637ef3a0a3c55045d2a))
* **panel:** resolve SPLIT audio from the on-air feed (server-side) ([#534](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/534)) ([#541](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/541)) ([72aab1b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/72aab1be0434c0729c31bc330c1d1bed485304b1))
* **relay:** use math.isfinite for the prebuffer NaN/inf guard (clears CodeQL) ([#533](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/533)) ([#540](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/540)) ([2b796cf](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2b796cf51d2a96360514e58c8e0a390da2a4e33d))

## [1.7.1](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.7.0...v1.7.1) (2026-07-17)


### Bug Fixes

* **event:** make event stop idempotent — a second stop is a no-op ([#524](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/524)) ([#526](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/526)) ([c7f8c6b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c7f8c6b2e7c5e3b50be9c0e70ee42de11620ba84))
* **panel:** program preview self-reschedules — no permanent wedge ([#520](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/520)) ([#521](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/521)) ([2d9d0aa](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2d9d0aa038b43683069691fa8649e54a7743caa2))
* **report:** gate the log bundle by session freshness ([#519](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/519)) ([#527](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/527)) ([504ac6e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/504ac6e8cae3fc1f2b137ebe3e1ec5d8745f7925))
* **report:** timeline shows the part label + keeps teardown events ([#523](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/523)) ([#525](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/525)) ([09af9c7](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/09af9c7e6641e9abdb3f8a238b148d1a364331d4))

## [1.7.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.6.0...v1.7.0) (2026-07-17)


### Features

* 12 new full-page Stint graphics (info + starting grid) ([#516](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/516)) ([f8d9618](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f8d9618a4b8a306f820ccc6162df1e99df6fa62c))
* **panel:** single-content Director Panel — drop PROGRAM/SETUP tabs ([#518](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/518)) ([e3dbf5d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e3dbf5d0c1ae4c21ddb77e14ced66864b7f00ed9))

## [1.6.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.5.5...v1.6.0) (2026-07-16)


### Features

* **panel:** Feeds block — ARM/STOP per feed + real served resolution ([#514](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/514)) ([18de150](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/18de150da49856092556d262782dfc111025c1a1))
* **relay:** /next auto-stops the freed feed + manual-arm default ON ([#489](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/489), [#492](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/492)) ([#508](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/508)) ([309e1ec](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/309e1ecf4e5589b0b34e3c8b557b8fce93420ec3))
* **relay:** classify source-not-live states + distinct reason + churn dampening ([#495](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/495) core) ([#502](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/502)) ([106b64a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/106b64a6ed13e4158718695cf0fcb8e5e452dc7d))
* **relay:** cursor-progress OBS freeze detector — instrument + auto-RESET ([#488](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/488)) ([#513](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/513)) ([6c46690](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6c466904e3f96505a08c4ef7207fd85ebb7cee99))
* **relay:** on-air auto-cover — raise the Standby Cover on an offline on-air source ([#495](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/495)) ([#507](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/507)) ([a1340e0](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a1340e07d3c147d62ca1eabbd52c9c1be11844c0))
* **relay:** opt-in two-stage feed scheduling — manual arm/disarm ([#492](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/492)) ([#499](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/499)) ([d56775d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d56775d1a7d031faf22c163f45d7a7875b842533))
* **relay:** robust ingest — quality profiles + auto-step-down ([#493](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/493)) ([#506](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/506)) ([99453df](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/99453dff4d01adffc70d95dab2abb5394d3d4889))


### Bug Fixes

* **relay:** auto-resync a drifting OBS feed via GetStats render-skip rate + graduated stall ([#488](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/488)) ([#504](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/504)) ([626e382](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/626e382932d3dd4e6a9d493bb0afb4927941e252))
* **relay:** detect + recover ping-pong/cockpit desync (Resync action + consistency guard) ([#494](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/494)) ([#498](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/498)) ([ac1861f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ac1861ff45d23b90d2ebc43356e202699e5e8db9))
* **relay:** enforce single-pull invariant on all feed-activation paths ([#491](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/491)) ([#496](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/496)) ([89a37d9](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/89a37d9a90995f3dc583a9bc06a45c169db902c6))
* **relay:** retry the sheet-webhook push with bounded backoff ([#490](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/490)) ([#501](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/501)) ([0f73089](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0f73089846aea5d5eb64123e7cbce81098d78982))
* **report:** attribute same-URL back-to-back stints distinctly + flag desync windows ([#500](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/500)) ([#503](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/503)) ([e09fe8b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e09fe8b2bcc2da72c80053bd8e43bc01e037a427))
* **ui:** profile-switch restart banner offers relay only, not Companion ([#512](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/512)) ([3744846](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3744846ef25221fe953eb9b84c6abc6cd1cecb42))

## [1.5.5](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.5.4...v1.5.5) (2026-07-11)


### Bug Fixes

* **producer:** pin gviz headers=1 so the Producer tab parses (Home card + Parts stream keys) ([#486](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/486)) ([bc77c2d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/bc77c2d838f3f0259a731646cb0a3dd19f05f0b1))

## [1.5.4](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.5.3...v1.5.4) (2026-07-11)


### Bug Fixes

* **media:** pass the real cookie jar to get-media so frozen Intro/Outro stop 403ing ([#482](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/482)) ([7444e60](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7444e606af2280e979a421a8ce02911f6f8336a4)), closes [#481](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/481)
* **obs:** dedupe Stint scene-item ids so HUD and Race Weather 1 stop cross-firing ([#479](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/479)) ([7951047](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7951047760af965e98ab28da8e7d114ffd07135a)), closes [#478](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/478)
* **report:** compute health/on-air bands per on-air window so uptime can't exceed 100% ([#483](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/483)) ([c5fe0ef](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c5fe0ef486cb2f11105dad80aa97d42de49d0796))


### Performance Improvements

* **relay:** cache the program-monitor screenshot to stop the obs-websocket connection storm ([#484](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/484)) ([ba0310e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ba0310e8508f262062c40773b175123b617c7a4d))

## [1.5.3](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.5.2...v1.5.3) (2026-07-11)


### Bug Fixes

* **console:** allow race/qualifying mode switch over the Funnel as a director op ([#470](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/470)) ([30ba47b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/30ba47bb58f570de0f2966ede3a3e8511ce46877))
* **relay:** make a qualifying-mode downgrade loud instead of silent ([#473](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/473)) ([5aa98f7](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/5aa98f7244e6b14a928c492bd27f3715c8b49c95))
* **relay:** record + report feed drop-recoveries, and [@here](https://github.com/here) on churn ([c382e45](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c382e4536e5282a888f1328d074973cc97d50947))
* **relay:** recover the on-air feed cleanly after a fan-out stream restart ([#475](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/475)) ([c9245e2](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c9245e2efac22b2eda294fc532110c75d8aa33e1))
* **relay:** reliably bring up the relay after clearing a stale port holder ([#468](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/468)) ([f005321](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f005321f978a8928ce6ef812e56cad8c3becc7ea))

## [1.5.2](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.5.1...v1.5.2) (2026-07-10)


### Bug Fixes

* **cloud:** harden cloud GPU box for unattended stop/start + preflight Companion probe ([#464](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/464)) ([3ae67ae](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3ae67aee5c1efd0ade1a4530a20f7fe16a7ea612))

## [1.5.1](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.5.0...v1.5.1) (2026-07-08)


### Bug Fixes

* **overlay:** let team logos align left/right in the builder (no more forced center) ([#451](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/451)) ([a865ee0](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a865ee05026d6115c9f8ce4eca38f0312615a886))
* **relay:** harden TEAM_NUMBER_RE against polynomial ReDoS (CodeQL [#170](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/170)) ([#453](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/453)) ([83ddb50](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/83ddb5069435d943ed339ebb119cbcae17e1648e))

## [1.5.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.4.0...v1.5.0) (2026-07-06)


### Features

* capture ad-hoc on-air stream substitutions (Discord + Director Panel + report) ([#418](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/418)) ([99f5b30](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/99f5b300b846ca3adb1cff62c97735df00ec5ebd))
* **cloud:** auto-reboot at end of provision (default on, PROVISION_REBOOT=0 opts out) ([#434](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/434)) ([f0e8a9d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f0e8a9de882920eebb82f8ebbaea105e786f54be))
* **cloud:** one-shot GPU-box provisioning script ([#395](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/395)) ([52c6943](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/52c69439736713b349bcdc13485b6708e13d04db))
* **cloud:** prepare-event.sh — per-event prep for the cloud producer box ([#443](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/443)) ([6c9b95c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6c9b95c84c9ced45575ba042c96a387eda5f7d85))
* **discord:** join a league voice channel via RPC (CLI + Control Center + auto-join) ([#427](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/427)) ([534783f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/534783f15217063f7f1ad2278393f45cff9cbafb))
* **event:** last-part end auto-stops the event with report + Discord; report fixes ([#439](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/439)) ([772ad4a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/772ad4a4271857967fdcf6a6015f7b0834647e0a))
* **event:** leave the Discord voice channel on event stop (mirrors auto-join) ([#440](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/440)) ([41c6a6a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/41c6a6ae4520599ae19ce19513c7953475e62c10))
* **event:** qualifying in the event start/stop lifecycle ([#441](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/441)) ([d8753cc](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d8753ccef23985ac356d7dc7578380ef4e40a066))
* hide OBS-only assets from the crew Graphics browser + add it to Director Panel & Race Control ([#415](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/415)) ([fa7919f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/fa7919f1225288c95abc8a8ff968b570b4c73c76))
* **install:** current yt-dlp binary + min-OS gate on Linux ([#412](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/412)) ([cc5a5e5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/cc5a5e51e17e94a335639fa5486ac209ec381239))
* **obs:** auto-switch OBS scene collection on event start ([#429](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/429)) ([a69037d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a69037d26b0af78c1d3dececae6a819906f78bac))
* **obs:** forced OBS refresh at event start + Director-Panel refresh action ([#436](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/436)) ([3a8caf6](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3a8caf67b689577ca5ca30bdb175493f5c04c276))
* **obs:** log OBS stream start/stop to the relay log (upstream kbps + uptime) ([#438](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/438)) ([0fd25a4](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0fd25a4e2988957cae391d842ee34cdc77be6002))
* **obs:** park OBS on Standby at event start (default-on, never cuts a live program) ([#437](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/437)) ([3603486](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3603486193a64ceae63781539098e85aca082cee))
* **panel:** Director-Panel broadcast Part control (start/stop Parts, headless SSH bring-up) ([#395](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/395)) ([#423](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/423)) ([5eadd25](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/5eadd259d6abf103d0973e5f30391d3ca6c0697a))
* **panel:** two-tab Director Panel layout (PROGRAM/SETUP) ([#419](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/419)) ([a26a732](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a26a7329b45a358d32c11b44968fbbcb4beccb02))
* **relay:** back-to-back stint continuity (same-URL feed, no dup pull / no cut) ([#417](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/417)) ([eca0727](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/eca07274ff545dc244a96b3aedade965d3091e44))
* **relay:** hide pure-placeholder graphics from the browser list ([#416](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/416)) ([2c0da7c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2c0da7cec54b2ca145f786090964ec5b9fef65c8))


### Bug Fixes

* **cloud:** auto-configure RustDesk in provision (password + ID on first boot) ([#432](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/432)) ([bcc6dd1](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/bcc6dd138ce9aaffd356836633ff599e7fe0e976))
* **cloud:** dedicated racecast user + provision robustness (install-to-home, ~/.config, set -e) ([#430](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/430)) ([337a6a7](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/337a6a7b6a8eeb462a76d5b6f4a7d6fafc37e290))
* **cloud:** harden group name (id -gn) + uniform SIGPIPE-safe snap probe ([#395](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/395)) ([ddd00eb](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ddd00eb703054d6c57e3c1c55c4584b6e624b228))
* **cloud:** install racecast as the login user (user-owned tree) + SIGPIPE-safe GPU probe ([#395](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/395)) ([71af210](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/71af21030212242a1295ba61043275b7a2cb83c0))
* **cloud:** ldconfig after the driver install so the NVENC verification is honest ([#433](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/433)) ([d812894](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d812894938f0456eea23686a49683e9dac989bcf))
* **cloud:** make the Linux/cloud producer bulletproof — streamlink floor, driver, headless X, PipeWire audio ([#395](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/395)) ([#422](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/422)) ([ab53eb3](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ab53eb3845edb97fbd56e0acca1a89912700acee))
* **cloud:** provision completeness — required Tailscale join + full verification gate ([#431](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/431)) ([14f3446](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/14f3446c8ccd8eecf49baedcf112d4688e3f517d))
* **discord:** close out voice-join follow-ups — bounded reads, non-interactive auto-join, read-only status (F1–F3 + cosmetics) ([#428](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/428)) ([7bcf6b6](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7bcf6b64f8c4f5f1e1fa17d8c7b2fb4e97d0970c))
* **install:** install libatomic1 before the companion-pi step on Linux ([#414](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/414)) ([ead6f50](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ead6f50762cda9771219306bc74de9d6122d5e9a)), closes [#413](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/413)
* **install:** run apt-get update before apt-get install ([#410](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/410)) ([6b35ecf](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6b35ecfdbf5b91db8fd09307d17fe36885030cbe)), closes [#408](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/408)
* **obs:** YouTube stream target needs a concrete ingest URL, not "auto" ([#399](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/399)) ([#435](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/435)) ([7656eb0](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7656eb0e1435ff66689a5d2254fd40bb28d345ab))
* **panel:** fold Part-control review follow-ups (share obs-ws poll, freeze modal Part) ([#395](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/395)) ([#424](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/424)) ([613481b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/613481b325ceaf7b961572b488ddfd8c151b4791))
* **panel:** unify race/qualifying into one mode-aware schedule block ([#444](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/444)) ([fd5e3f4](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/fd5e3f424393b0e8be5dddf28fadc7ac0cf3ba89))
* **preflight:** re-tune CPU/RAM thresholds + GPU-aware core floor ([#395](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/395)) ([#425](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/425)) ([e850f36](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e850f36c7184c01fe830c53f6b52c96ccd51f8cd))
* **report:** scope the post-event report to the current event, bundle logs + hostname ([#442](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/442)) ([85e1098](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/85e10987a950cf555b88eb5e853f7296c1e16c88))
* **ui:** style the report Download link as a button ([#420](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/420)) ([ce9de6b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ce9de6b21c7e691a2bd7cb3fab1c1d4fbc38c319))

## [1.4.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.3.0...v1.4.0) (2026-07-02)


### Features

* **brands:** per-league brand-logo override via Sheet + profile export ([#370](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/370)) ([e1b5d78](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e1b5d786106a4b07a4b87b544690fc0e5fe8dac0))
* **cockpit:** commentator → director cue-back ([#377](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/377)) ([#380](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/380)) ([6136b58](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6136b583021be1b7ff1451a787c0fbbe75516bc0))
* **console:** event notes — Sheet tab + toggleable modal in director panel, cockpit & race control ([#383](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/383)) ([6179e83](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6179e8338f66975670974135e3a1e4fbe9beac48))
* **console:** producer role implies director + race_control ([#374](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/374)) ([0acf1bf](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0acf1bfbd04ce8e7bca0067eec2f293cade3f051))
* **console:** version badge + Help button on Director Panel, Cockpit & Race Control ([#375](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/375)) ([3778bdd](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3778bdd541879a1e3875d3044666d30a44a1a7df))
* flag-status graphics (parallel to the flag-text chip) ([#372](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/372)) ([cdb9b85](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/cdb9b85c935c0d85f1989a24e06d5ab879eba27e))
* Intermission scene — graphic + looping music + broadcast-chat box ([#368](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/368)) ([f4e3f28](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f4e3f28ef38e2d43889ac5b91312456ebfc45e93))
* **obs:** director per-take transitions — Cut/Fade/Stinger on the Director Panel ([#390](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/390)) ([b06ed8e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/b06ed8e3140d42d62e7877c0a9419f6cda5572f9))
* **obs:** Sheet-driven OBS stream target (service + key per Producer Part) ([#399](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/399)) ([44d7606](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/44d7606723f3e8eb296061a34c1e95737f078bbd))
* on-air program-audio monitor (Director Panel / Cockpit / Race Control) ([#405](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/405)) ([cdbbf43](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/cdbbf430aa6dacc023135c24c1be3780354e61f4))
* **panel:** batch-apply Top-3 teams in one action (no live duplication) ([#373](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/373)) ([fcb648a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/fcb648a8c1043742b8016ed8c0d091d423d92372))
* **panel:** move Cues to the top, between PGM and Live Preview ([#392](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/392)) ([a2649f5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a2649f581288e7beef725b7dbf20be12303bdc80)), closes [#385](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/385)
* **race-control:** Race Control → commentator info channel ([#376](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/376)) ([#379](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/379)) ([5167c71](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/5167c71c34dff88312f91525e60d678bdeb74295))
* **relay:** auto-failover to the Intermission scene on confirmed on-air feed loss ([#382](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/382)) ([11c6335](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/11c63350622a46b2a3239b1dc337ce85986a12f4)), closes [#378](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/378)
* **relay:** default RACECAST_FEED_FANOUT to on (live-verified), keep the switch ([#365](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/365)) ([21ea8f3](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/21ea8f328de805efa2e498e4af9c14db08a56ea6))
* **relay:** relay-side feed fan-out — fix stale-on-activation glitch + free preview ([#358](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/358)) ([#360](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/360)) ([49df8f7](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/49df8f78a2d5db7bfd3cef02597ce4ee3a3bdf4a))
* **report:** post-event report — CLI + Control Center view + Discord send ([#388](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/388)) ([f593219](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f593219288d250f737c9c0b795518e0cfd07ab12))
* **ui:** live resource monitor — Control Center System card + health-history charts ([#389](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/389)) ([f2755d3](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f2755d33252aad141171ea399e7fe0faa487f0d4))


### Bug Fixes

* **assets:** handle Google Drive's modern &lt;form&gt; download interstitial ([#393](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/393)) ([308b1f9](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/308b1f9db936568cc18878a22e82e5e8df263b33)), closes [#386](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/386)
* **assets:** reset graphics/media to placeholder when the Sheet drops a link ([#394](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/394)) ([b500567](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/b5005670dd7b15b9ade15cb5c6aa2d9b90e48d37)), closes [#387](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/387)
* **broadcast-chat:** recover a frozen YouTube reader + Refresh button ([#294](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/294)) ([#362](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/362)) ([5d1f1c3](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/5d1f1c33311e0871dcec767a13da6fe105b6181f))
* **broadcast-chat:** Refresh button shows a "Refreshing…" state ([#363](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/363)) ([0200a00](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0200a0046d0881cc8a7e5d1af15eda822cb1ce43))
* **console:** style the Event Notes modal Close button ([#391](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/391)) ([e74e375](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e74e3758d33fc34e2661a437c277c62d6af45d52)), closes [#384](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/384)
* **director:** style transition Duration input + add UI visual-verification gate ([#397](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/397)) ([#401](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/401)) ([c228c29](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c228c298207f94571741e92180b9304c668dd533))
* **intermission:** rename background source off scene-name collision; skip music in get-graphics ([#371](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/371)) ([c72d191](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c72d191212eb94bb07c5c4e5a5e75af72f47ca7c))
* **obs:** per-scene HUD groups so splitscreen CURRENT/NEXT labels survive import ([#366](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/366)) ([1957463](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/19574639cfaa8456ef942b52ef0ff295e034b444))
* **overlay-builder:** allow deselecting a slot (back to nothing-selected) ([#396](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/396)) ([2dc1942](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2dc194272574dd922a08154c1dd70d6077001c09))
* **relay:** silence self-healed fan-out reconnect health pings + log streamlink stderr ([#367](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/367)) ([0165cc0](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0165cc02096c02cc595e68d5b188c5662a646fc7))
* **resources:** resolve CodeQL py/mixed-tuple-returns + post-merge security-check hook ([#400](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/400)) ([82426c5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/82426c546af02a24ac341d7b47ed529e76d630ef))
* **tooling:** self-gate the post-merge reminder hook on the Bash command ([#402](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/402)) ([418ca7f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/418ca7f424ef20408f8c53b699e69d453dddb8aa))
* **ui:** show a preview tile for audio media (Intermission Music) ([#403](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/403)) ([a29c0ce](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a29c0ce123d5c8a7fbbbe8cb81604e12d51ece50)), closes [#398](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/398)
* **ui:** stop overlay-builder Fit-mode scrollbar jitter on Windows ([#369](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/369)) ([44a276a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/44a276a3e22e4901479a37a2d167b99911087974))

## [1.3.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.2.0...v1.3.0) (2026-06-28)


### Features

* **assets:** neutral placeholders for missing OBS graphics/clips ([#357](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/357)) ([ecffd3c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ecffd3c0fe7f9eb7e057f0c36cb7bd119477310a))
* **broadcast-chat:** compose-popup button (native YouTube/Twitch chat) ([#355](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/355)) ([b19b195](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/b19b195d23c56e4fe1b1befdd980bab181183a9c))
* **broadcast-chat:** render custom/image emotes (YouTube + Twitch first-party) ([#353](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/353)) ([b83df0c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/b83df0c2e4e953ed651a19330e106bb9a8256c3e))
* **broadcast-chat:** render standard YouTube emoji as Unicode glyphs ([#345](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/345)) ([fab2739](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/fab273979c34116e390dcb571948fff88d51958a))
* **cockpit:** read-only stint plan below the race timer ([#354](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/354)) ([86946b2](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/86946b2bc666b3360f9a8be6582a8822326df959))
* **preview:** usable Director Panel live preview — per-second stills + off-air audio meter ([#359](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/359)) ([1b7fc58](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/1b7fc58848961f84fd8304201a04c8a88f5688ba))


### Bug Fixes

* **broadcast-chat:** make the /panel compose button clickable on desktop ([#356](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/356)) ([e2ddff4](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e2ddff4e03a020a5909cc3b0533fdc9819d1fd1f))
* **media:** retry get-media downloads on transient YouTube HTTP 403 ([#348](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/348)) ([ce45c93](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ce45c93f89e966ceaae7ea18c579df0990882e5a)), closes [#344](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/344)
* **relay:** give streamlink yt-dlp's UA + cookies on the YouTube serve ([#350](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/350)) ([385bde2](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/385bde29b42f2ab6aeb9a95d7a36a355f658973d))

## [1.2.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.1.0...v1.2.0) (2026-06-26)


### Features

* **builder:** align toolbar, undo/redo, and snap grid for the overlay builder ([#333](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/333)) ([afc7873](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/afc78739f8b0d192aa8ccf27ae604f930db600bb))
* **builder:** Bold / Italic font controls in the overlay builder ([#330](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/330)) ([756cf3b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/756cf3b6645356b120373f056a86763ff9389f38))
* **console:** Funnel auto-enable defaults on (opt-out) ([#332](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/332)) ([ba1f469](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ba1f469d5c814533fc4e73d9b04a3a4d35e9abff))
* **hud:** color-coded race-condition flag (Sheet / Panel / Companion) ([#331](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/331)) ([aeed4b8](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/aeed4b8e2e8808482fe77512cdad490d2b1909d6))
* **hud:** per-team Brand Name element + builder hide/show toggle ([#328](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/328)) ([6824f92](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6824f92227c49d8bbb51acf3722e9bf934add796))
* **overlay:** align caution flag colors + checkered pattern + Code 60 emblem ([#339](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/339)) ([a2e6d7f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a2e6d7f49e21a54732ae6c73f3bda8ee838b2b18))
* **overlay:** canvas zoom + slanted (parallelogram) builder edges ([#338](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/338)) ([842ed2a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/842ed2a4a4810a55190f18ff9e39a4d7ff200718))
* **overlay:** editable Clock field in the builder Preview-data panel ([#343](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/343)) ([acb035e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/acb035e6df75e7798a9c7b1a2d4eb88f9b54b076))
* **overlay:** editable Preview-data panel in the visual builder ([#340](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/340)) ([df9dd0e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/df9dd0e541a18d5a5f29064c9683fb32822f1524))
* **overlay:** sync the per-league POV box to the OBS Feed POV source ([#337](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/337)) ([c25a32a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c25a32aec90b66286d3b031bdd056ac3336986ba))


### Bug Fixes

* **overlay:** self-host true font weights & styles (real bold/italic) ([#342](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/342)) ([9c3be3c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/9c3be3c159c01b13b71b2b472fc3c20a1c740a4c))
* **panel:** keep car number in team dropdowns; strip only in HUD ([#341](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/341)) ([70c0b05](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/70c0b05a120c6224558fcadc81e8e497c45dfb47))
* **sheet:** make the race-condition Flag setup-write deployable ([#336](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/336)) ([d8b9d15](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d8b9d151b58c7ca49c2326be4def182d239a113a))

## [1.1.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.0.1...v1.1.0) (2026-06-25)


### Features

* **cockpit:** compact two-column cockpit layout ([#310](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/310)) ([55b1e73](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/55b1e732ea93968785ed896f9a9c83f06ec7207f))
* **console:** On-air stint/streamer banner on the Race Control desk ([#299](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/299)) ([4ea70ae](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/4ea70aefc0db0b3b9ba24ebf7d3a3f48cb91e2cb))
* **console:** read-only YouTube broadcast-chat in /console pages ([#296](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/296)) ([57c4e73](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/57c4e73d18f68cc0c2b63af77a3eca8fee55b470))
* **console:** Twitch support for the broadcast-chat reader ([#297](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/297)) ([ba3478f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ba3478f089358060bd0c2d865ae0fcadc1365266))
* **events:** producer takeover + OBS stream start/stop notifications ([#317](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/317)) ([#323](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/323)) ([e6aeebe](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e6aeebe6cb0fcfeff201fe23cd76e20e77a53111))
* Health Monitor dashboard (relay-served, SQLite time-series, /console) ([#282](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/282)) ([4cf6ac6](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/4cf6ac6e8dfeb025f25f46aa5fab1222c88ae6ed))
* **health:** Health Monitor extensions — OBS stats, connectivity, feed quality ([#283](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/283)) ([0ce12ad](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0ce12ad4ee41fe64bca384b2beb4844f861a8609))
* **obs:** bake 300 ms Fade show/hide into POV feed + Stint graphics ([#293](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/293)) ([8858c10](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8858c10e8d417d867eb412562ca6227a03e52b58)), closes [#291](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/291)
* **panel:** Start/Stop the OBS stream from the Director Panel ([#295](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/295)) ([#298](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/298)) ([33170f8](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/33170f859eed241a7248aea891873919ffccf54e))
* **preview:** embed probable next release version in preview identity ([#286](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/286)) ([a3b54a1](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a3b54a15dfac4ae91fb0ef84a5cadbfea2dab779))
* **relay:** self-healing relay start (converge to one current relay on 8088) ([#289](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/289)) ([e3456c9](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e3456c927c7693ea3475f28b84e025492398d13a))
* **ui:** Funnel takeover toggle on the Control Center Home ([#288](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/288)) ([e57ff5f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e57ff5fdde033721b0e226697d1af7f17ca9536b))
* **ui:** Kill stale relay button (Control Center) ([#284](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/284)) ([2e3a697](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2e3a6973ab531e1bde8ded8ac28506a0c706066f))
* **ui:** persist Control Center action output to runtime/logs/app.log ([#316](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/316)) ([94a300a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/94a300a129223c3d80445971091f0cd0e215c79c))
* **ui:** Producer schedule + one-click takeover on the Control Center Home ([#292](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/292)) ([2706e4a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2706e4a0f02682117318fb015d4cf3c0342cdfa4))


### Bug Fixes

* **build:** tolerate a transient localhost timeout in the racecast-ui smoke test ([#315](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/315)) ([41e9e98](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/41e9e98b251fddca1ce632c079ab34b4e0e71964))
* **console:** add Health Monitor card to the /console launcher ([#290](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/290)) ([839e4f2](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/839e4f2e605031a24fd1a48652544dcc52971f0d))
* **console:** fixed-height scrolling chat boxes + cockpit side-by-side layout ([#309](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/309)) ([d2fa742](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d2fa742c076646f971a8faf91406dab0160c248c))
* **console:** strip the auth token from the URL on all token-bearing pages ([#325](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/325)) ([e75e543](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e75e543a98176f8b6a9170def0e38a277f5cc6dc))
* **logs:** throttle + URL-shorten the streamlink pump (stops 662 MB feed-log flood) ([#318](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/318)) ([8738a3c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8738a3cc114c55c79cad2c26dfae18e1ef1a095f))
* **obs:** clean obs-websocket closing handshake (1000, not 1006) ([#321](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/321)) ([8aaf1ee](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8aaf1ee575cfd14f40c0a99d6911772147df0509))
* **panel:** pin director chat rail to fixed-height, no-shrink boxes ([#311](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/311)) ([4661752](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/4661752db0f62d4a396d5f3ef849673028c8bd56))
* **preview:** force-move the rolling tag so previews sort correctly ([#287](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/287)) ([6ff9880](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6ff988023f86e2a0e5a2d33894b94f3707c02318))
* **racecast:** silence CodeQL dead-store on producer-name cache ([#326](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/326)) ([dc0f5c1](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/dc0f5c144709385a3c954801fdc5c2ee3657b64b))
* **relay:** debounce false CRITICAL feed-down health pings ([#280](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/280)) ([40a4289](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/40a42893cb2b195fd7721dc905041a07b445363f)), closes [#278](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/278)
* **relay:** escalating backoff + idle-after-N for dead stint serves ([#320](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/320)) ([dc453bc](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/dc453bc1647c927fd64193315f0eecc524ccb050))
* **relay:** probe the control port before startup work; show league in start log ([#319](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/319)) ([5a911ac](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/5a911acb230f7ba56b21cecbdae7aa2343827ee2))
* **security:** close code scanning alerts (response splitting + repeated import) ([#285](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/285)) ([04b0bcc](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/04b0bccd9c1c99e62a520b4dd4de524b2488f604))
* **takeover:** authorize /console/takeover/* by the step-up secret alone ([#312](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/312)) ([3e66592](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3e665929d7221ca62e2ed8970298ab6288f85700))
* **ui:** drop stale OBS credentials from the Director Panel link ([#322](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/322)) ([4fab4bd](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/4fab4bd69ababacf6e7c83284bd6e3f1216ae42e))

## [1.0.1](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v1.0.0...v1.0.1) (2026-06-22)


### Bug Fixes

* **relay:** make the relay control port a true singleton across profiles ([#276](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/276)) ([eccb3ef](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/eccb3ef8fa80d82a9a2ce7bb96686546ea3df2b6)), closes [#273](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/273)
* **ui:** bust Control Center Assets gallery cache on profile switch ([#275](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/275)) ([64527b0](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/64527b0c5f3832550b1c8ae96a6c67c5d7c7de53)), closes [#274](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/274)

## [1.0.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.9.0...v1.0.0) (2026-06-22)


### Features

* **assets:** complete the HUD asset set — full country flags + canonical Lamborghini brand name ([#258](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/258)) ([f47c464](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f47c464051eba10d4a529440b487caea8b60fe6f))
* **cockpit:** broadcast-graphics browser (list + open in new tab) ([#268](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/268)) ([a2d66e5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a2d66e5ff6dc00a1e38da0e8c6716fb713385760))
* **docs:** centralize the role cheat sheet on the onboarding decks ([#271](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/271)) ([56def65](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/56def65c731e8a74f7a0ac4a5f7857d752219fd7))
* **hud:** adopt the de-branded demo overlay as the base standard ([#260](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/260)) ([a581381](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a581381949d18eac2f9d38cf31657688029b583c)), closes [#206](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/206)
* **init:** point the profile step at the demo profile for a smoke test ([#262](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/262)) ([ba68340](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ba68340361fb067220109a18ed90ac77842a0af4)), closes [#206](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/206)
* **profiles:** ship a directly-usable de-branded demo league + Sheet template docs ([#257](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/257)) ([be3845a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/be3845ac927534aa6092ea1e93cbb593ae870a03))
* **slides:** onboarding slide decks + Director pilot ([#263](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/263)) ([17ece27](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/17ece27bb4e463dc9a9a7de5180ffc7aad7731fe))


### Miscellaneous Chores

* release 1.0.0 ([82fd6ab](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/82fd6abaea29e061b05481b5be7750fadfe57d21))

## [0.9.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.8.0...v0.9.0) (2026-06-21)


### Features

* **cli:** `racecast links` (Crew ∪ Schedule) + Crew Console ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216) phase 5) ([#231](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/231)) ([cd2586e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/cd2586e5044ab0de0ca5ee3bc6369362638fd8c0))
* **cli:** public Funnel mounts /console + `racecast funnel` command ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216) phase 4) ([#230](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/230)) ([e998d4b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e998d4b3c817bfe5940692f8b9479297d57974c8))
* **console:** Companion Web Buttons over the Funnel (/console/buttons) ([#241](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/241)) ([81e3a05](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/81e3a0570cefd6ec7de515570adc998a890fc430))
* **console:** Discord OAuth login for /console + cockpit→console rename ([#242](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/242)) ([f751e76](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f751e766b34dde8d8a565ee5c11da513e9509d51))
* **console:** distribute the shared console link from Crew Console (Copy / Post to Discord) ([#249](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/249)) ([89439e5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/89439e597a5349ccb1937bb02f05aec076acd120))
* **console:** Race Control crew role — read-only monitoring desk ([#244](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/244)) ([#248](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/248)) ([7fe05c5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7fe05c554655af9f7202bcbc70bf0edd33fa5ead))
* **event:** producer takeover over the public Funnel ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216)) ([#234](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/234)) ([7901eca](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7901eca7d621b90ff982df0aa52daf0d461b22b5))
* **panel:** preview button on pending stream submissions ([#247](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/247)) ([48f892f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/48f892f09e6d46f64b20bae1d7d18d037e18c663)), closes [#245](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/245)
* **panel:** relay-mediated OBS control — credential-free, Funnel-complete Director Panel ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216)) ([#238](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/238)) ([5cc7333](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/5cc733310ba4e11ac1fc13f94c214a2ae19eb613))
* **relay:** /console auth gate + role-gated API mirror ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216) phase 3a) ([#228](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/228)) ([d9c4cc5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d9c4cc5538d1ce9628ab61582dba832efc246bd7))
* **relay:** /console authorization policy ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216) phase 2) ([#227](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/227)) ([3f62ed8](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3f62ed8507e343c8732cd63b66ad57c290bd835a))
* **relay:** crew roster + role resolution ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216) phase 1) ([#226](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/226)) ([e7c5f2e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e7c5f2e6fcf1ba0af1a1d3a971848654d978122a))
* **relay:** director→talent text-cue channel (IFB-lite) ([#243](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/243)) ([#246](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/246)) ([06fcd90](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/06fcd90a647baa66291dbc61187603908d737e7f))
* **relay:** role-adaptive /console pages + launcher ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216) phase 3b) ([#229](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/229)) ([280d7ac](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/280d7acf89b9aee58221d4fc27e5ece56225672f))
* **ui:** Control Center crew editor for the Crew-tab roster ([#216](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/216)) ([#233](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/233)) ([7e7460a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7e7460a6691cbbf6d350f2e6e59dd406488e1c6b))
* **ui:** optional "stop event" on Control Center quit, waiting for completion ([#221](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/221)) ([1766287](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/176628700f163a891d84e9fef5592620cd9434d0)), closes [#218](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/218)
* **ui:** replace native confirm()/alert() with custom Control Center modals ([#224](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/224)) ([42a1cae](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/42a1cae4a1030fd34d759d3b07a8cdf3018e0c6f))


### Bug Fixes

* **logs:** keep timestamped logging through a blocked Windows log rotation ([#223](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/223)) ([152f80a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/152f80a8db915c749b92dd6f786d511ecdfed871))
* **relay:** resolve py/mixed-returns in do_GET/do_POST (CodeQL) ([#232](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/232)) ([1bab505](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/1bab505aefce38121f7b70f7e9b0e93b7c064e09))
* **ui:** style Control Center event-title input (was browser default) ([#225](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/225)) ([37d7652](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/37d76526bb279c872bdbc2c57c0f7f35c325c9ca))
* **ui:** widen Crew editor actions column so Save/Delete fit the card ([#251](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/251)) ([27c50a6](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/27c50a6db0299a9f3813544c0208f892719b33a5))

## [0.8.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.7.0...v0.8.0) (2026-06-18)


### Features

* **cockpit:** Commentator Cockpit — talent monitor, tally, chat & timer ([#191](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/191)) ([75dafc6](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/75dafc6c000ceb884804f4cb2150825835e4c3eb))
* **cockpit:** commentator stream-link submission with director approval ([#202](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/202)) ([c429bfe](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c429bfeda9285b64801e81f94fb5f923cfeb23de)), closes [#193](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/193)
* **cockpit:** Discord note (no [@here](https://github.com/here)) when a stream link is approved ([#205](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/205)) ([c9cf02d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c9cf02db92f410416a7476680549236103fa4ef9))
* **cockpit:** zero-config — remove the enable flag, auto-provision the secret ([#215](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/215)) ([07db52b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/07db52b89121308078f2e7f21623fc153120e9e5))
* **e2e:** drive the harness against the frozen binary (binary-only bug guard) ([#212](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/212)) ([f9da5f5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f9da5f5b098db99bdab918007a56eea73d0b2842))
* **e2e:** e2e/regression harness — drive relay + cockpit + Control Center headlessly ([#208](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/208)) ([007fa33](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/007fa332ab6147d045b57790db3dc72a718b7667))
* free-text event title across Director Panel, Cockpit & Discord ([#209](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/209)) ([c97b63c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c97b63c29c07d13e35c7129ec1ac615661b491ca)), closes [#207](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/207)
* **logs:** timestamps, daily rotation + 7-day cleanup, archive browsing, OBS/Companion/Tailscale sources, and an aggregated live view ([#217](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/217)) ([3b4626e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3b4626e1f3f66da06911413f54895e1894b6ddb0))
* **ui:** "Copy internal link" option in the Control Center Cockpit view ([#213](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/213)) ([106bed1](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/106bed131f66b98683d481cb507da2d0c029692b))
* **ui:** editable event title on the Control Center Home view ([#207](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/207)) ([#210](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/210)) ([da02a74](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/da02a7490bff2ab226df4a2e1722deb4b7e8711b))


### Bug Fixes

* **binary:** bundle src/cockpit/ so /cockpit loads in frozen builds ([#211](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/211)) ([035cb26](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/035cb268f94c233229b85a5f0c9e2127302204ad))
* **cockpit:** tear down the funnel with `funnel reset` ([#201](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/201)) ([7c2806a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7c2806abbd49ee364b8e21b8ca6e6a127fe816e9)), closes [#200](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/200)
* **logs:** close tail_merged handles via `with open()` (CodeQL py/file-not-closed) ([#220](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/220)) ([8337557](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8337557c04775d9f921e2e8c226fe135fca897f3))
* **logs:** resolve CodeQL alerts from [#217](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/217)'s tail_merged + close the gate gap ([#219](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/219)) ([c45abf7](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c45abf7c65f284347b4f7bc72aae30fcfffabd7d))
* **panel:** clear only the URL in the schedule editor, keeping streamer + stint ([#203](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/203)) ([ec6fe42](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ec6fe4298f89552a583a35775a9b1bb5dbbeb493))
* **security:** resolve open code-scanning alerts + add procedure-return lint guard ([#204](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/204)) ([1945134](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/1945134e4f8523237f738262498cc7b9916017ce))

## [0.7.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.6.0...v0.7.0) (2026-06-16)


### Features

* **event:** one-command producer takeover ([#189](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/189)) ([594f5cb](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/594f5cb4c38a0e723493cd17f759e1ee52a49ef1))
* **event:** pre-flight gate before event start bring-up ([#185](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/185)) ([32e8431](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/32e8431fcd4c88e1f0047ef1b4cb2395e6bda68d))
* **panel:** add a "D" favicon to the director panel ([#194](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/194)) ([8527f2e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8527f2e311dcaf549bd520c5ee3e185a3ab663fd))
* **panel:** live preview multiview in the director panel ([#190](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/190)) ([0e3bed4](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0e3bed42a6e9323f9b7644ec77862589ce4e069d))
* **relay:** [@here-ping](https://github.com/here-ping) the crew on every Discord health change ([#196](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/196)) ([af37e2b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/af37e2bb321ed4dbf9668b318bd68112baedddc0))
* **relay:** feed-down alert when a live feed drops unexpectedly ([#186](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/186)) ([ab05810](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ab058108fce2c207668dac1ee46373b4bb6a148b))
* **relay:** live health heartbeat with Discord alerts ([#188](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/188)) ([0011833](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0011833362074eb9645aa9502922e360cafe44f9))


### Bug Fixes

* **relay:** send a User-Agent so Discord health alerts are not 403'd ([#195](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/195)) ([e3fbed5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e3fbed579bee44fc40711c2a66e3521bd5dd4865))

## [0.6.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.5.0...v0.6.0) (2026-06-16)


### Features

* **profile:** open the league Google Sheet from CLI + Control Center ([#183](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/183)) ([8153469](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8153469df21046b363037d609faa4029c9636f62))


### Bug Fixes

* **companion:** scrub _MEIPASS off LD_LIBRARY_PATH for bare systemctl in frozen binary ([#180](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/180)) ([9b274cc](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/9b274ccf9fb5a17239c944658eeff7831e01e7b5))

## [0.5.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.4.0...v0.5.0) (2026-06-16)


### Features

* bandwidth speed test option (CLI + Control Center) — closes [#131](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/131) ([#160](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/160)) ([1290787](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/1290787a0feeb881dab1d91f2b3d9367de6420d6))
* **companion:** control the companion-pi systemd service on native Linux ([#174](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/174)) ([bd3a843](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/bd3a8430ba79e654141d28f1e9d64ae01726a4a5))
* **install-tools:** sudo apt + auto-install deno on Linux ([#163](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/163)) ([4916402](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/491640224bc306cd82a024a804d1c8a09fae1af1))
* **obs-browser:** build & install the Browser Source plugin from source on Linux ([#177](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/177)) ([6d40604](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6d40604114901e72ae5cce96b25cbab41849ed82))
* **obs:** Discord-web browser audio fallback for Linux without native Discord ([#179](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/179)) ([3837f1d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/3837f1d339c85208c3ac0a4b9eb03dc8bcf93c88))
* **overlay:** label splitscreen feeds CURRENT/NEXT ([#129](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/129)) ([#156](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/156)) ([615b9b5](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/615b9b51935c84d2cf049e9a1696edde91225b9c))
* **overlay:** POV box name + relay-driven PiP toggle ([#158](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/158)) ([a2e46ab](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a2e46ab4fd3d4af84e00a5a5aa17dce88b270880))
* **overlay:** standard properties for all builder slots ([#176](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/176)) ([1def62b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/1def62b486e7ed9c4d2617517e0361f7af743558))
* **release:** build ARM64 Linux binaries ([#161](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/161)) ([fe2b828](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/fe2b828c081ef7dabf5ccef59f3ffc082f12ff77))


### Bug Fixes

* **frozen:** sanitize subprocess env for external tools (LD_LIBRARY_PATH leak) ([#165](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/165)) ([497c3a2](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/497c3a2431d0ba746807b228f11f7b3af8246fa2))
* **frozen:** strip all _MEIPASS dirs from the external-tool spawn path ([#166](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/166)) ([1252e78](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/1252e78b19e4dcbe49944246e422bd804cc77673))
* **install-apps:** clean env for vendor scripts + skip Discord on ARM64 Linux ([#168](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/168)) ([01d616e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/01d616e569d2c58b919218a78809f2206aee5f86))
* **install-apps:** send a real User-Agent for vendor downloads (Discord 403) ([#167](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/167)) ([8e7eaeb](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8e7eaebfa91dfd96294fe06a411cd62772974df0))
* **panel:** style schedule + qualifying streamer dropdowns like the HUD section ([#154](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/154)) ([444d235](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/444d2356bd6c149adaf46b698d841e2bc8875954)), closes [#152](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/152)
* **preflight:** don't blame Sheet sharing for a network timeout ([#169](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/169)) ([0f88f2f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/0f88f2ff83d42bf2f240a16e5203ad1d06d044d8))
* **racecast:** consistent returns in companion_start/stop (py/mixed-returns) ([#178](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/178)) ([f36ca2e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f36ca2e1c1a00f23adc111851456d2ed2ccf5ef7))
* **tailscale:** correct Linux UX — no GUI app, first login is `sudo tailscale up` ([#170](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/170)) ([e1c66f1](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e1c66f1bf2eefd7b5fa38f15591214385a8ce066))
* **tailscale:** Linux operator hint + document the Linux first-login ([#171](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/171)) ([d3cdb06](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d3cdb06e7af8007734a2891f3d3c5a4a592e2b36))
* **ui:** refresh asset gallery on profile switch/import ([#164](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/164)) ([6c23129](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/6c2312945aec75f238efd328c8b3819208d09b39)), closes [#162](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/162)

## [0.4.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.3.0...v0.4.0) (2026-06-14)


### Features

* **fonts:** bundle curated overlay fonts into builds, auto-seed on start ([#132](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/132)) ([bc2a916](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/bc2a91655364e7f5b29bfd4bed6876c140868f51))
* **overlay:** editable timer position, split team slots, slot picker, POV box ([#146](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/146)) ([dbeef73](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/dbeef7368f4e5181cf40cc2fb34d8b63a9be6e5d))
* **overlay:** merge race timer into HUD, POV frame, property prefill ([#153](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/153)) ([ed27bbf](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ed27bbfa360926d783b580873e3d81e94f65a160))
* **relay:** qualifying mode — separate tab served on Feed A ([#127](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/127)) ([02263ee](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/02263ee46448abf2942217b49edb87f4d91cf388)), closes [#124](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/124)
* **relay:** schedule-driven HUD stint & streamer on handover ([#125](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/125)) ([cc4695b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/cc4695b370bafbab9cbc83d0ba1f941db20867b3)), closes [#112](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/112)
* **relay:** show pre-planned stints (blank URL) in the Director Panel ([#148](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/148)) ([e7da64d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e7da64df86f4e011e687812b98655ffd07efbfe4)), closes [#137](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/137)
* **streams:** add a freeport recovery action for the feed ports ([#142](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/142)) ([9c9a038](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/9c9a0384b00bfdc6d88c8c8c87c5d97dcbd3d860))
* **ui:** visual overlay builder for league overlays ([#114](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/114)) ([#128](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/128)) ([cf05ebf](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/cf05ebf164aa11ffddc034f9f9d19476b44c85b1))


### Bug Fixes

* **lint:** enforce CodeQL py/empty-except locally; comment 5 silent swallows ([#150](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/150)) ([2f97cea](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2f97cea1062c09f4e3b8185288cafa9d50ba18bd))
* **panel:** style the Qualifying section like the Schedule section ([#144](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/144)) ([4475729](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/4475729e574e465a21de8072fe035547b3461436)), closes [#134](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/134)
* **relay:** surface an immediate feed bind failure into /status last_error ([#147](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/147)) ([1f106de](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/1f106de9fc2dabeab6fc478e371a46d9d98c0b85)), closes [#143](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/143)
* **ui:** font library rows show one self-rendered preview, no overlap ([#149](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/149)) ([e84a3ed](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e84a3ed19e123f7d3d2c2da34073d5fd0f2cb0fc)), closes [#145](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/145)

## [0.3.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.2.0...v0.3.0) (2026-06-13)


### Features

* Twitch parity across relay and static mode ([#105](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/105)) ([13c4c0e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/13c4c0e276dda31242458f7d71971b4be9f32c5e))


### Bug Fixes

* **services:** verify process identity before kill ([#118](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/118)) ([a0e360e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a0e360e2d696a64792d8e56561d7812a8dfa30c4))
* **ui:** render release notes as plaintext + add CSP ([#117](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/117)) ([85b498d](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/85b498d63cfe9afe8c48324dadfa7f86ecd0e35b))
* **ui:** restrict machine .env editor to RACECAST_ keys ([#120](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/120)) ([7ccef1f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7ccef1f748ece8c5b6c2c86826d8558a91995ba5))
* **update:** filter tar symlinks + cap decompression ([#116](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/116)) ([b6ac332](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/b6ac33221b6864b1b2817ceeebecb9aeea5f266a))

## [0.2.0](https://github.com/jegr78/gt-endurance-racing-broadcast/compare/v0.1.0...v0.2.0) (2026-06-13)


### Features

* **chat:** crew chat in the director panel ([#72](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/72)) ([8806c44](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/8806c44d4d01eed602adda0c6558decc63b97b8e))
* **hud:** HUD design preview — overlay over a GT backdrop ([#85](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/85)) ([#90](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/90)) ([8253989](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/82539894e8ad73d182359dd7ae2ca9a2326c7b5c))
* **install-apps:** show installed app versions (CLI + Control Center) ([#97](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/97)) ([2c8c189](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/2c8c189a61ea8423c38f6668c9d58f88293f0acb))
* **panel:** persistent right-column chat on desktop + styled Send ([#82](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/82)) ([#89](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/89)) ([c79c471](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/c79c471df23c25a3d1da38e44df8f8715c608c00))
* per-league overlay identity — team name/number split + panel Top-3 ([#80](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/80)) and look backup/restore ([#81](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/81)) ([#86](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/86)) ([61b2529](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/61b25298c2f56e9c0c50c03339898e711da62e46))
* **profile:** export/import a whole league profile (producer onboarding) ([#93](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/93)) ([800903c](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/800903c537d2ad8c1b80b4a7e68d2220e600126c))


### Bug Fixes

* **install-apps:** don't fail --update on apps installed outside Homebrew ([#96](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/96)) ([e6d0174](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e6d0174243f8f97d328aa4d56c04e5ea3988c9e0)), closes [#92](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/92)
* **panel:** write Top-3 teams to the Setup tab, not the Overlay tab ([#95](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/95)) ([7c749ae](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/7c749aeaff290c57a0fc5e4a3d0db0422bfe4922))
* **relay:** clear Race Control on the /next handover cut ([#107](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/107)) ([89ebb35](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/89ebb35a48f50f8ecd61f9bbe1d23aeb3a3e3cbc))
* **relay:** live OBS-reachability probe in /status (no stale 'OBS NOT REACHABLE' banner) ([#94](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/94)) ([ec7031e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ec7031ed08da98670e1a6c6eba7e1ca79f80d24b))
* **relay:** make the loopback bind mandatory — no silent split-brain ([#84](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/84)) ([#87](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/87)) ([d8fe085](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/d8fe08523e72d335a29e86e4e41cdb5b6b964c35))
* **relay:** no visible console window when starting the daemon on Windows ([#110](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/110)) ([e77442f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/e77442f27e3436d64f99695002137d27c614532a))
* **security:** close CSRF→RCE, update integrity, SSRF + arg-injection (review [#1](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/1)–[#4](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/4)) ([#106](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/106)) ([befefbd](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/befefbd6d8c8cde564e4a8aaeabfb326652d0556))
* **security:** document the two best-effort empty-except blocks (CodeQL) ([#79](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/79)) ([a58cc13](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/a58cc137290dafe6d982d17cdcad6cf1fc2757cb))
* **ui:** offer updates on frozen preview/dev builds in the Control Center ([#75](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/75)) ([69a5f6f](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/69a5f6f583aa7bd68aa53f7f82779d99985f4a07))
* **ui:** open the Director panel on the Tailscale host when available ([#83](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/83)) ([#88](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/88)) ([db0418e](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/db0418ed9276f71317800733f721595042416583))
* **ui:** refresh active-profile env in Control Center preflight/asset checks ([#108](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/108)) ([b47ce33](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/b47ce33e7cf6b461337c1af75bb07cdf958436f9))
* **ui:** suppress Windows console flash from in-process probes ([#109](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/109)) ([95e5329](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/95e5329cb41ce4fa7d4228d1a3b7eb1adbb4558c))
* **update:** self-update can replace the running Control Center on Windows ([#111](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/111)) ([32e001a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/32e001a7b708d5ccec41cae8779dd65addafb7aa))

## 0.1.0 (2026-06-11)


### Features

* **build:** embed the racecast app icon in the binaries ([#58](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/58)) ([#67](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/67)) ([4c25fc8](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/4c25fc85bc84bbc8e099ee3e45ea033e456e1636))
* **ui:** add a racecast favicon for the Control Center ([#57](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/57)) ([#66](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/66)) ([ebbd931](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ebbd931df45989036dae66dd14f54257cd9ba6a5))
* **ui:** remove the monogram badge from the Control Center header ([#69](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/69)) ([f2cd3e1](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/f2cd3e179c3df978bf44e4e96b4902437d3b0aca))
* **ui:** show the active league logo in the Control Center header ([#60](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/60)) ([bfc09b7](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/bfc09b723f71ef07297a6728e085ba1cacc2cc62))


### Bug Fixes

* **docs:** drop the stale "v4 setup" version from the cheat sheet ([#56](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/56)) ([#65](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/65)) ([ad75a99](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ad75a99ed6ec0c72ec01df6c6009cab1669d260c))
* **ui:** inject active-profile env in the windowed launcher ([#54](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/54)) ([#63](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/63)) ([ee8c45a](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ee8c45ac2fe9da958981a57e28993ba91e1340ae))
* **ui:** resolve asset-serving root live per request, not at startup ([#55](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/55)) ([#64](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/64)) ([ff3413b](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/ff3413bf35335648b556727502995af3ff2dac86))


### Miscellaneous Chores

* pin the next release to 0.1.0 ([94a4148](https://github.com/jegr78/gt-endurance-racing-broadcast/commit/94a414871a3c73356f1e9a3131595520f6c5aa42))

## Changelog

<!-- Maintained by release-please. The first 0.1.0 entry is written by the bot
     on the first release after the version baseline reset. Earlier history
     (the IRO line and 1.x/2.0.0) lives in docs/CHANGELOG-archive.md. -->
