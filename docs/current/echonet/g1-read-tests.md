# G1 — Test specification and cases

| ID | Case | Expected |
|---|---|---|
| G1-01 | initial_state with `0279:2` and `027D:3` | instances preserved |
| G1-02 | missing PV or battery | explicit TopologyError |
| G1-03 | duplicate target class | ambiguity error |
| G1-04 | requestId response matching | only matching future completed |
| G1-05 | command_result success=false | error code/message preserved |
| G1-06 | malformed JSON/schema | GatewayProtocolError |
| G1-07 | request timeout | GatewayTimeoutError |
| G1-08 | property_changed | cached property updated |
| G1-09 | device_offline | cached device marked offline |
| G1-10 | get_properties | EPCs encoded as uppercase two-digit hex |
| G1-11 | real Raspberry Pi + RC-307A | `0279:*` and `027D:*` observed through echonet-list |
| G1-12 | disconnect/restart | no false online status; reconnect plan validated |

G1-11/12 wait for Raspberry Pi hardware. Unit/contract fixtures do not require LAN hardware.