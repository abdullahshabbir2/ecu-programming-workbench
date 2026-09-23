# Supported diagnostic and transport profile

## CAN and ISO-TP

| Item | Value |
|---|---|
| Addressing | Normal, physical, 11-bit CAN IDs |
| Tester TX / ECU TX | `0x700` / `0x708` |
| Frame format | Eight-byte classical CAN payload, zero padded |
| PDU size | 1-4095 bytes |
| Receiver flow-control block size | 8 CFs by default; zero means unlimited |
| Receiver STmin | 1 ms default, integer range 0-127 ms |
| Transport timeout | 100 ms |
| Maximum WAIT responses | 3 per transmitted PDU |

CF sequence is modulo 16. Out-of-order, unsolicited or interleaved transport traffic is rejected. RX deadlines restart after valid CF reception. TX waits for a new FC when its granted block is exhausted. The scheduler advances in integer milliseconds; it does not model arbitration, bit timing, interrupt latency or CAN controller errors. Microsecond STmin values, CAN FD, extended/mixed addressing and functional broadcast are unsupported.

## Services

| SID | Service | Profile |
|---|---|---|
| `10` | DiagnosticSessionControl | Default 1, programming 2, extended 3; advertised P2=50 ms, P2*=1000 ms |
| `3E` | TesterPresent | `00` with response; `80` suppresses positive response |
| `22` | ReadDataByIdentifier | F190 lab identity; F181 active version; F1A0 active image SHA-256 |
| `19` | ReadDTCInformation | Subfunction 02 with status mask; synthetic records only |
| `27` | SecurityAccess | 01 requests 16-byte nonce; 02 submits 16-byte lab HMAC |
| `31` | RoutineControl | Start FF00 resets inactive transfer; start FF01 validates and stages image |
| `34` | RequestDownload | DFI=00; ALFID=44; 4-byte address and size; big endian |
| `36` | TransferData | 8-bit block counter, 1-64 data bytes |
| `37` | RequestTransferExit | Complete declared transfer length required |
| `11` | ECUReset | Hard-reset subfunction 01 activates a verified staged bank |

Programming address is the owned virtual window at `0x00100000`, maximum 65536 bytes. It is not a physical ECU address. Erase in this model invalidates transaction state; it does not emulate sector erase pulses. Response `74 20 00 42` permits 66 request bytes including SID and sequence counter.

The server accepts an exact duplicate of the last transfer block without appending again, enabling recovery from a lost acknowledgement. A repeated counter with different bytes is rejected. Counters begin at 1 and wrap through 0. The client retries only the same TransferData request once after a diagnostic timeout; erase and reset are not automatically retried.

## State model

```mermaid
stateDiagram-v2
    Default --> ProgrammingLocked: 10 02
    ProgrammingLocked --> Unlocked: valid lab challenge response
    Unlocked --> ErasePrepared: 31 01 FF00
    ErasePrepared --> Downloading: 34
    Downloading --> Downloading: 36 / exact duplicate acknowledgement
    Downloading --> TransferComplete: 37 / exact byte count
    TransferComplete --> VerifiedStaged: 31 01 FF01 / integrity + version
    VerifiedStaged --> Default: 11 01 / active pointer commit
    Downloading --> Default: session expiry
```

Engine speed must be zero and voltage 11.0-15.0 V for every programming service. An interlock violation aborts the transaction and sets a synthetic diagnostic. Session idle timeout is 2000 ms. Validation routine FF01 emits NRC `78` followed by completion after 30 simulated milliseconds. Client pending responses are limited to eight and the request has an overall deadline.

## Owned LAB1 image

All integer header fields are big endian.

| Offset | Size | Meaning |
|---:|---:|---|
| 0 | 4 | ASCII LAB1 |
| 4 | 4 | Positive monotonically increasing version |
| 8 | 4 | Payload byte count |
| 12 | 32 | SHA-256 of payload |
| 44 | N | Nonempty payload |
| 44+N | 4 | zlib CRC32 of header and payload |

Integrity records are not authenticity credentials. The HMAC demonstration key is published in source; deployment key management, signed images and tamper-resistant rollback counters are absent.
