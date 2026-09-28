# Mobile Workspace

Mobile Workspace runs the open-source **mobile-use** agent against one persistent, local Android emulator. The Mobile Agent is a plugin node with a Workspace capability; its task execution uses the saved workflow, node configuration, and authenticated principal. The emulator belongs to the OpenCompany installation and is shared across Mobile nodes and workflows. Tasks queue for this device.

## Supported deployment

The initial implementation supports **Android on a Windows host**. The OpenCompany backend, Android SDK, emulator, device broker, and mobile worker must run on the same host. A remote browser can display the Workspace, but does not supply the emulator. Only the installation owner can access Mobile Workspace.

iOS is not enabled. Future iOS support requires a same-host macOS backend and Apple's simulator tooling; installing this plugin on Windows does not provide an iOS simulator. Physical devices, remote device farms, multiple concurrent emulators, and distributed mobile workers are outside this implementation.

The runtime singleton and its leases are process-local. Run one backend process with its local Temporal activity worker; multiple API processes or an independently deployed Android activity worker do not share this device broker. Merely placing separate processes on the same machine does not make their runtime state shared. A future multiprocess deployment needs a dedicated broker service and explicit authenticated routing to it.

## Setup and use

1. Install `uv` for the optional Python environment and enable the host virtualization support required by Android Emulator. Setup downloads pinned Android command-line tools and a private Temurin Java runtime when needed. An existing SDK can be selected with `ANDROID_SDK_ROOT` or `ANDROID_HOME`; the standard Windows Android SDK location is also detected.
2. Add a Mobile Agent node, save the workflow, and open its Mobile Workspace. The setup diagnostics inspect SDK tools, emulator acceleration, the system image, engine, and video server.
3. Review and accept the Android SDK license in setup. Setup installs the optional engine and Android packages and creates the persistent `OpenCompany` AVD using a Pixel 7 device profile. Network access and sufficient disk space are required for these downloads.
4. Start the shared device. The emulator runs without a separate desktop window; the Workspace shows its video. Device storage persists across stopping and starting the emulator.
5. Take manual control to sign in to the Play Store, install applications, or upload an APK. APK uploads are limited to 256 MB and require the current manual-control lease. Play Store availability comes from the selected Google Play system image; account sign-in is performed on the device.
6. Connect a provider in OpenCompany Settings, then connect exactly one AI model node to the Mobile Agent's Model input and save the workflow. The model selected on that connector is used for every mobile-use stage. OpenAI, Anthropic, and Gemini credentials are supported. Choose a model with image input and tool use, then submit a task from Workspace or delegate from an employee.

The setup is optional. Ordinary application startup does not install the mobile engine or boot an emulator. Runtime data lives under the configured OpenCompany data directory's `mobile` subdirectory, including the engine environment, AVD, and device resource metadata. Stopping the emulator does not reset its apps or account sessions.

## Control and task lifecycle

Video is a read-only scrcpy transport. Mouse, touch, text, navigation, rotation, APK installation, and agent mutations go through the server's device-control broker rather than the video socket. The initial preview supports one browser tab at a time.

Taking control revokes the agent's lease and capability before stopping its worker, then waits for an already admitted device mutation to drain. A monotonically increasing epoch rejects delayed inputs from an old controller. Operation identifiers deduplicate successful mutations within the current lease. An uncertain device failure fences control and requires recovery rather than replaying the mutation automatically.

Resuming starts a fresh agent worker against the current device state with a new capability. This is not continuation of an in-memory model conversation. The remaining active time and step budgets are carried forward. A task can wait for human control for up to 30 minutes. Cancelling a queued task prevents it from starting; cancelling a task while a person controls the device preserves that person's lease.

Workspace submissions use a client-generated submission UUID and a durable Temporal workflow identity scoped to principal, saved workflow, and node. Repeating a submission returns the existing task; reusing its UUID with a different prompt is rejected. Mobile task activities have automatic retries disabled. This is not a guarantee that arbitrary device actions are globally exactly-once across host failure.

## Engine isolation and credentials

OpenCompany installs mobile-use into its own Python 3.12 environment. The main server does not import its agent dependencies. Each task receives a separate worker process; a trusted device process owns the explicitly selected emulator serial. The worker's injected controller talks to a loopback capability broker. It does not choose an ADB serial or receive an arbitrary host-command API.

A maintained source patch injects the controller before mobile-use's normal device initialization and redirects screenshot and platform-helper calls. It blocks SDK-side app installation and disables dotenv loading. The patch fails closed when its expected source structure changes. The upstream SDK's cancellation method alone is not used as proof that device work has stopped.

The node resolves the selected provider's existing default-account credentials on the server and passes only that provider credential in the worker configuration. Credentials are not supplied by the browser and are not included in task status. Device processes receive a filtered environment. Agent telemetry and trace recording are disabled by this integration. Task screenshots and prompts still go to the configured model provider as part of inference. Custom provider base URLs are not currently exposed by the Mobile Agent configuration.

## Dependency pins and attribution

The optional installer verifies downloaded artifacts before extraction or execution:

| Component | Pin |
| --- | --- |
| mobile-use source | `62913c933e21b27da353a89316a09f60525af496` |
| Source archive SHA-256 | `e2bac1f1d47e895d8c9ec840b0e896391ffd664ed2d4149a1ff1744252ca8384` |
| Python dependencies | Upstream lockfile, installed with `uv sync --frozen --no-dev` |
| scrcpy server | `4.1` |
| scrcpy artifact SHA-256 | `deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae` |
| Android command-line tools | Windows revision `15859902` |
| Command-line tools SHA-256 | `90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a` |
| Private Java runtime | Temurin JRE Windows x64 `21.0.12.1+1` |
| Java archive SHA-256 | `d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636` |
| Android system image | `system-images;android-36;google_apis_playstore;x86_64` |

The Android package identifier selects an SDK package, not an immutable revision checksum. Updating the engine pin requires reviewing the patch, its bypass coverage, and the upstream lockfile together. Source archive extraction preserves upstream legal files; mobile-use is Apache-2.0 licensed, and its attribution and NOTICE must remain with redistribution.

## Local integration versus Minitap services

[mobile-use](https://github.com/minitap-ai/mobile-use) is the open-source Python agent and CLI reused here. The embedded preview, lease broker, takeover behavior, optional installation, and Workspace task routing are OpenCompany integration code.

Minitap's [platform](https://www.minitap.ai/platform#slack-cli-mcp) and [miniTest documentation](https://www.minitap.ai/docs/minitest) describe separate hosted testing products, including managed runs and integrations. This local plugin does not automatically provide their device fleet, hosted test reports, Slack integration, or hosted MCP service, and does not require a Minitap account for local engine execution.

## Validation and limitations

Automated tests exercise fencing, successful-operation deduplication, cancellation while draining, takeover and resume with fake subprocesses, queued cancellation, source-patch compatibility, and Python syntax compilation of the patched upstream package. Additional tests use the actual loopback HTTP broker with a fake driver, reject unsafe archives and checksum mismatches, and verify that SDK tools run through Java arguments without a command shell. Patch tests require `.tmp/mobile-use-upstream` or `MOBILE_USE_UPSTREAM_SOURCE` pointing to the pinned checkout; they skip when that fixture is absent.

A local smoke test executed `worker.main` profile construction, SDK initialization, a fake task, and cleanup using the actual pinned dependency environment in `.tmp/engine-smoke` and a fake controller. It made no model request and connected to no emulator.

These tests do not validate a real emulator, Play Store login, APK execution, model inference, hardware acceleration, or browser video decoding. Validate those on a supported Windows host before treating this as production device automation. Current task results contain a response and run identity; the artifact list is empty, and hosted-style videos or reports are not generated.

## Planned iOS implementation

This is an implementation plan, not currently supported functionality. Keep the Workspace capability, task submission, ownership checks, and lease protocol shared, and add platform-specific lifecycle, driver, installation, and video adapters.

1. Add a macOS-only setup diagnostic for Xcode selection, accepted Apple licenses, installed simulator runtimes, and the chosen simulator device type. Setup must ask for the required Apple license acceptance and must not promise unattended Xcode installation.
2. Create or select one managed simulator by persisted UDID using `xcrun simctl`; explicitly boot it, wait for readiness, and shut down only the simulator owned by this installation. Never infer the target from whichever simulator happens to be booted. Preserve simulator data across normal restarts.
3. Implement an iOS driver in a separate trusted process on the same Mac. Evaluate and pin the upstream simulator controller's IDB companion requirements. Route its screenshots, hierarchy reads, gestures, text, app lifecycle, and platform helpers through the same lease checks as Android. Do not allow the agent worker to discover devices or spawn its own companion.
4. Add a simulator video adapter with a defined framing and geometry contract. Android scrcpy is not an iOS transport. Validate encoding, browser decoding, rotation, coordinate scaling, disconnect cleanup, and backpressure on the supported macOS and browser versions before enabling live preview.
5. Accept simulator-compatible application builds and install them through a lease-protected `simctl` operation. Do not describe arbitrary physical-device IPA files or iOS App Store installation as supported simulator workflows. Display platform-specific app prerequisites and controls.
6. Extend the pinned upstream patch and smoke tests for the iOS context, then execute the acceptance matrix below on a real Mac. Keep the iOS Workspace entry disabled until the lifecycle, automation, and takeover checks pass together.

## Acceptance matrix

| Area | Automated coverage now | Required device acceptance |
| --- | --- | --- |
| Setup | Checksums, archive safety, license refusal, Java argument boundary | Clean Windows x64 setup, acceleration diagnostics, SDK download failure and retry |
| Agent adapter | Pinned-source patch compilation and real-dependency fake-task smoke | Image-capable model completes a task through the broker |
| Device lifecycle | Runtime tests use fake processes | Start, stop, crash, restart, persistent apps and session state |
| Ownership | Lease fencing, drain, deduplication, queue cancellation, scoped identities | Two browser tabs, delayed input, takeover during a real device mutation |
| Human resume | Fake subprocess termination and new capability | Agent resumes from the manually changed screen within remaining budgets |
| Video and input | Transport code is separate from mutation broker | H.264 decoding, rotation, scaled pointer coordinates, typing, disconnect cleanup |
| Android apps | APK admission and installation are broker-owned | Install a simulator-compatible APK; manually authenticate and install from Play Store |
| iOS | Not implemented | Same-Mac lifecycle, IDB adapter, simulator app install, preview, gestures, fenced takeover |

Passing a source or fake-process test does not count as passing the corresponding device acceptance check.
