"""The `otadata` partition: which app slot the bootloader starts, and its state.

Two 4 KB sectors, each may hold one esp_ota_select_entry_t (32 bytes):
ota_seq u32, seq_label[20], ota_state u32, crc u32 where crc is the CRC-32 of
ota_seq seeded with 0xFFFFFFFF (zlib.crc32(seq, 0xFFFFFFFF), checked against
the core's boot_app0.bin). The bootloader takes the valid entry with the highest
sequence that is not INVALID or ABORTED; slot = (seq - 1) % number_of_slots.

Writing a new choice into the sector that is NOT the current winner, and only
then, is what makes a switch atomic: until the new sector is complete the old
choice still wins (docs/OTA.md, the journal's step C3).
"""
import struct
import zlib

SECTOR = 0x1000
SIZE = 2 * SECTOR
NEW, PENDING_VERIFY, VALID, INVALID, ABORTED, UNDEFINED = 0x0, 0x1, 0x2, 0x3, 0x4, 0xFFFFFFFF
NAMES = {NEW: "NEW", PENDING_VERIFY: "PENDING_VERIFY", VALID: "VALID", INVALID: "INVALID",
         ABORTED: "ABORTED", UNDEFINED: "UNDEFINED"}


def crc(seq):
    return zlib.crc32(struct.pack("<I", seq), 0xFFFFFFFF)


def entry(seq, state):
    return struct.pack("<I20sII", seq, b"\xff" * 20, state, crc(seq))


def read(data):
    """[(sector, seq, state, valid)] for both sectors."""
    out = []
    for i in (0, 1):
        seq, _, state, c = struct.unpack("<I20sII", data[i * SECTOR: i * SECTOR + 32])
        out.append((i, seq, state, seq != 0xFFFFFFFF and c == crc(seq)))
    return out


def winner(data, slots=2):
    """(sector, seq, slot, state) the bootloader would pick, or None (no valid entry: app0)."""
    best = None
    for sector, seq, state, ok in read(data):
        if ok and state not in (INVALID, ABORTED) and (best is None or seq > best[1]):
            best = (sector, seq, (seq - 1) % slots, state)
    return best


def switch_to(data, slot, state=NEW, slots=2):
    """A new otadata that starts `slot` next, written into the sector that does not
    hold the current winner. The old winner is left untouched."""
    cur = winner(data, slots)
    base = cur[1] if cur else 0
    seq = base + 1
    while (seq - 1) % slots != slot:
        seq += 1
    other = 1 - cur[0] if cur else 0
    out = bytearray(data)
    out[other * SECTOR:(other + 1) * SECTOR] = entry(seq, state).ljust(SECTOR, b"\xff")
    return bytes(out)
