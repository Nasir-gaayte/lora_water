/**
 * LoRaWAN Payload Decoder — Water Meter C2 Protocol (V1.06)
 *
 * Paste this function into your Network Server console
 * (TTN / ChirpStack / Helium / AWS IoT Core for LoRaWAN).
 *
 * Protocol frame layout (AFN = 0xC2):
 *   Start(1) | Length(1) | Address(5) | AFN(1) | Payload(20×N) | Timestamp(6) | CS(1) | End(1)
 *
 * Single-hour payload (20 bytes):
 *   [0..2]  Forward Instantaneous Flow  — 3-byte HEX LE, ÷1000 → M³/h
 *   [3..6]  Forward Cumulative Flow     — 4-byte HEX LE, ÷10   → M³
 *   [7..8]  RTU Battery Voltage          — 2-byte BCD LE, ÷100 → V
 *   [9..10] Meter Sensor Voltage         — 2-byte BCD LE, ÷100 → V
 *   [11..12] Status Flags                 — 2-byte HEX bitmask
 *   [13..15] Reverse Instantaneous Flow  — 3-byte HEX LE, ÷1000 → M³/h
 *   [16..19] Reverse Cumulative Flow     — 4-byte HEX LE, ÷10   → M³
 *
 * Timestamp (6 bytes BCD at end of payload, before CS):
 *   ss mm hh DD MM YY
 */
function decodeUplink(input) {
  var bytes = input.bytes;
  var data = {};

  if (bytes.length < 14) {
    return { errors: ["Frame too short"] };
  }

  // Optional: validate start code 0x68 / function code 0xC2
  // (index 0 = 0x68, index 6 = 0xC2 for single-hour)
  // if (bytes[0] !== 0x68 || bytes[6] !== 0xC2) {
  //   return { errors: ["Invalid protocol frame"] };
  // }

  // --- helpers ---

  function parseHexLE(start, length) {
    var value = 0;
    for (var i = 0; i < length; i++) {
      value |= (bytes[start + i] << (i * 8));
    }
    return value >>> 0; // unsigned
  }

  function parseBcdLE(start) {
    var lo = bytes[start];
    var hi = bytes[start + 1];
    var d1 = lo & 0x0F;
    var d2 = (lo >> 4) & 0x0F;
    var d3 = hi & 0x0F;
    var d4 = (hi >> 4) & 0x0F;
    return (d4 * 10) + d3 + (d2 * 0.1) + (d1 * 0.01);
  }

  function parseBcd6(start) {
    var bcd = "";
    for (var i = 0; i < 6; i++) {
      bcd += ((bytes[start + i] >> 4) & 0x0F).toString(16);
      bcd += (bytes[start + i] & 0x0F).toString(16);
    }
    return bcd; // ss mm hh DD MM YY (2 digits each)
  }

  // --- payload at offset 7 (past header) assuming single-hour snapshot ---

  data.forward_instantaneous_flow = parseHexLE(7, 3) / 1000.0;
  data.forward_cumulative_flow = parseHexLE(10, 4) / 10.0;
  data.voltage_rtu = parseBcdLE(14);
  data.voltage_meter = parseBcdLE(16);

  // Status bitmask (2 bytes at index 18)
  var status = parseHexLE(18, 2);
  data.alarms = {
    memory_alarm:      (status & 0x0001) !== 0,  // BIT0
    flow_meter_alarm:  (status & 0x0002) !== 0,  // BIT1
    low_battery:       (status & 0x0020) !== 0,  // BIT5
    valve_open:        (status & 0x0040) !== 0,  // BIT6
    magnetic_attack:   (status & 0x0080) !== 0,  // BIT7
    leakage:           (status & 0x0100) !== 0,  // BIT8
    pipe_burst:        (status & 0x0200) !== 0,  // BIT9
    validity_invalid:  (status & 0x8000) !== 0,  // BIT15
  };

  data.reverse_instantaneous_flow = parseHexLE(20, 3) / 1000.0;
  data.reverse_cumulative_flow = parseHexLE(23, 4) / 10.0;

  // Timestamp (6 bytes at index 27)
  var ts = parseBcd6(27);
  // ts = "ssmmhhDDMMYY"
  data.meter_timestamp =
    "20" + ts.substr(10, 2) + "-" +
    ts.substr(8, 2) + "-" +
    ts.substr(6, 2) + "T" +
    ts.substr(4, 2) + ":" +
    ts.substr(2, 2) + ":" +
    ts.substr(0, 2) + "Z";

  // Multi-hour support: if payload has multiple 20-byte blocks
  // increment start offset by 20 for each additional hour

  return { data: data };
}


/**
 * Downlink encoder — Valve control (AFN = 0xD7)
 *
 * For TTN / ChirpStack use the downlink formatter:
 *   function encodeDownlink(input) { … }
 *
 * Control bytes:
 *   0x7E = Open valve,  0x69 = Close valve
 *
 * Frame: 68 0D <ADDR> D7 <CT> <BCD_TIMESTAMP_6B> CS 16
 */
function encodeDownlink(input) {
  var action = input.data.action; // "open" or "close"
  var deviceEui = input.data.device_eui; // hex string, 8 bytes
  var ct = action === "open" ? 0x7E : 0x69;

  // Build address bytes from EUI
  var addr = [];
  for (var i = 0; i < 5; i++) {
    addr.push(parseInt(deviceEui.substr(i * 2, 2), 16));
  }

  // Current time as BCD (ss mm hh DD MM YY)
  var now = new Date();
  function bcd2(v) { return ((Math.floor(v / 10) << 4) | (v % 10)); }
  var ts = [
    bcd2(now.getUTCSeconds()),
    bcd2(now.getUTCMinutes()),
    bcd2(now.getUTCHours()),
    bcd2(now.getUTCDate()),
    bcd2(now.getUTCMonth() + 1),
    bcd2(now.getUTCFullYear() % 100),
  ];

  // Assemble frame: 68 L ADDR AFN CT TS CS 16
  // L = 5 (addr) + 1 (AFN) + 1 (CT) + 6 (TS) = 13 = 0x0D
  var frame = [0x68, 0x0D].concat(addr, [0xD7, ct], ts, [0x00, 0x16]);

  // CRC-8 with poly 0xE5 over addr + AFN + CT + TS
  var crc = 0x00;
  for (var j = 2; j < frame.length - 2; j++) {
    crc ^= frame[j];
    for (var k = 0; k < 8; k++) {
      if (crc & 0x80) {
        crc = ((crc << 1) ^ 0xE5) & 0xFF;
      } else {
        crc = (crc << 1) & 0xFF;
      }
    }
  }
  frame[frame.length - 2] = crc;

  return { bytes: frame };
}
