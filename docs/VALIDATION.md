# Validation and integration record

Executed environment: Python 3.12 on Windows. `python verify.py` is the repeatable entry point. The current result is recorded in `evidence/tests.txt`; tests are actual executions, not planned outcomes.

| Requirement | Test evidence |
|---|---|
| Correct PDU reconstruction across boundaries | `test_boundary_lengths_and_counter_wrap`, 30 seeded random roundtrips |
| Bounded transport waits | Flow-control, consecutive-frame, WAIT and late-frame tests |
| Session/security enforcement | Session gates, invalid keys, lockout, expired challenge and replay tests |
| Voltage/engine interlocks | Interlock boundary and mid-transfer voltage-loss tests |
| Idempotent transfer retry | Lost acknowledgement and duplicate-payload tests |
| Correct block wrap | Update longer than 256 blocks |
| Reject invalid image or rollback version | CRC failure and version tests; active bank remains unchanged |
| Preserve diagnostic records | Before/after DTC comparison on completed update |
| Bounded diagnostic response handling | Pending flood, unrelated response and timeout tests |
| Recover interrupted commit | Reopen BankStore after three injected PowerLoss boundaries |

## Commit decisions

1. All new bytes are written to the inactive bank.
2. The image is validated and read back before it becomes eligible for activation.
3. The pointer is replaced only during the reset service.
4. After interruption before pointer replacement, the previous image remains selected.
5. After interruption after replacement, the new verified image is selected.
6. Corrupt active metadata fails closed; the code does not silently factory-reset.

`fsync` is applied to file content and `os.replace` is used for pointer replacement. Directory durability, concurrent programmers, physical flash erase geometry and sudden electrical power loss are not covered. Tests inject exceptions at explicit software boundaries; they are not physical power-cut tests.

## Bench integration plan — not executed

Use an authorized lab target and documented controller/transceiver wiring. Define CAN bitrate, termination and grounding from the actual hardware manuals; there is deliberately no invented MCU pinout here. Replace VirtualBus with a validated transport adapter while retaining service-level fixtures. Capture an independent CAN trace and compare request/response content, timings and reset behavior. Exercise voltage and power interruption with the target's documented limits, then confirm active-image identity and diagnostics after restart.

## Release gaps

- No target driver, hardware flashing or CAN electrical validation.
- No full ISO-TP/UDS conformance suite or OEM service definitions.
- No signed secure boot, secret storage or reboot-persistent lockout.
- No real-time, endurance, environmental or safety qualification.
- The CI template in `ci/github-actions.yml` is supplied but not enabled or executed on GitHub.
