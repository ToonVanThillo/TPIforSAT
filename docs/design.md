# TPI for SAT: design

A Versatile Thermostat (VTherm) plugin algorithm that drives TRV valve openings so they work *with* a modulating boiler controlled by Smart Autotune Thermostat (SAT), not against it.

## Context

- SAT sets the flow temperature from a heating curve (outdoor temperature) plus a PID on room error.
- VTherm controls each room's TRV (Sonoff TRVZB) as an *over_climate thermostat with direct valve regulation*: it writes the TRV's opening and closing degree entities.
- VTherm's built-in TPI assumes a fixed flow temperature. With SAT, the valves should stay as open as possible so SAT sees the real demand, and only throttle a room when the flow is too hot for it.

## Control law

Per thermostat, on every update:

```
e   = target - room                          # K
v_p = 1 + c_int·e + I                        # unclamped valve demand
v   = clamp(v_p, 0, 1)                       # on_percent sent to VTherm
```

`I` is the integral state, kept in [-1, 0]: 0 means "fully open", -1 means "fully closed at target". Between updates `I` evolves as:

```
saturated = (v_p ≤ 0 and e < 0) or (v_p ≥ 1 and e > 0)
if not saturated: I += k_i·e·dt              # anti-windup: don't integrate into a saturated valve
I -= I·dt / tau_leak                         # leak toward open, so a too-high SAT curve shows up as a warm room
I  = clamp(I, -1, 0)
```

- **Time step**: `dt` is the measured wall-clock time since the previous update, in hours. It isn't VTherm's cycle length, because in valve mode VTherm calls the algorithm on every temperature or target change, not on a fixed cycle. The interval is integrated with the error and saturation state that held *during* it, i.e. those of the previous update. `dt` is capped (30 min) so a stall can't inject a large step.
- **Not heating**: when the thermostat isn't in heat mode (off, sleep, cool, …), VTherm reports an off reason (e.g. window open), or power shedding is active, the output is 0 and `I` is frozen (no integration, no leak). The timestamp is cleared so the next active update doesn't integrate the frozen period. This takes precedence over a missing temperature.
- **Missing temperature**: while heating but without a target or room temperature, the output is `None` (no demand known; VTherm keeps the current valve position) and `I` is frozen as above.
- **Restore**: setting `I` from storage clamps it to [-1, 0] and clears the timestamp, so the first update after a restart only records the time.
- While frozen, the diagnostics `e` and `v_p` are unset and `saturated` is false.
- **Periodic update**: the leak changes the output even when the room temperature doesn't, and VTherm only re-evaluates the algorithm when a temperature changes. The handler therefore re-evaluates it every 5 min while in heat mode.
- **Defaults**: `c_int = 2` (1/K), `k_i = 1` (1/(K·h)), `tau_leak = 8` h.

## Integration into VTherm

The plugin is a separate HA custom integration, `tpi_for_sat`, using VTherm's prop-algorithm plugin mechanism (VTherm ≥ 10.6.3, interfaces from the `vtherm_api` package ≥ 0.7.0; the prop-algorithm contract is unchanged up to 0.10.0). It follows the same structure as KipK's smartPI plugin:

- `manifest.json` declares `dependencies: [versatile_thermostat]` and requires `vtherm_api>=0.7.0`.
- Requires Home Assistant ≥ 2026.9.0: flow schemas use `probatio`, HA's validation library since that release.
- On setup it registers a factory named `tpi_for_sat` with `VThermAPI.get_vtherm_api(hass).register_prop_algorithm(...)`. It registers again when VTherm reloads its API. The name then appears in VTherm's "proportional function" choice for each thermostat.
- The factory creates one **handler** per thermostat. The handler is the VTherm glue (lifecycle, persistence, attributes, periodic update) and places the **algorithm** object on `thermostat.prop_algorithm`.
- The algorithm (`calculate(...)` → `on_percent`) is plain Python with no HA imports, so it can be tested in isolation and reused in the simulator.
- `calculate` accepts VTherm's positional and keyword arguments and ignores unknown keyword arguments, for forward compatibility.
- Target thermostat type: over_climate with direct valve regulation. over_valve thermostats use the same algorithm interface and are expected to work, but aren't the design target (VTherm doesn't pass power shedding to the algorithm there).
- In valve mode, VTherm passes `on_percent` through its valve filters and on to the TRV's opening and closing degrees. The plugin doesn't touch the valve entities itself.

## Persistence

`I` is stored per thermostat in an HA `Store`, keyed by the thermostat's `unique_id`, so renaming a room doesn't lose state. It is loaded when the thermostat is added; an unreadable or malformed file logs a warning and starts from `I = 0`. Whenever `I` changes, a debounced save (300 s) is scheduled, and the save is immediate on removal; HA flushes pending saves at shutdown. When VTherm reloads a thermostat, the new handler continues with the removed handler's `I`, handed over in memory. The last-update timestamp isn't persisted; after a restart the first update only sets it.

## Configuration

Each thermostat using `tpi_for_sat` needs its own config entry. HA only loads the integration, and so only registers the algorithm with VTherm, when at least one entry exists. The config flow selects the target thermostat and sets `c_int`, `k_i` and `tau_leak` (≥ 1 h, so the leak step stays accurate at the 0.5 h dt cap). The options flow edits them later. A change applies to the running thermostat immediately and doesn't reset `I`. Parameters must be finite. If a thermostat's entry is missing, disabled or deleted while the integration is loaded, it runs on the defaults and logs a warning. Reloading an entry keeps its parameters throughout.

Handlers belong to VTherm thermostats, not to our config entries, so shared runtime state (running handlers, and integrals handed over during a VTherm reload, by thermostat `unique_id`) lives in a typed `hass.data` key and not in `entry.runtime_data`.

## Diagnostics

Each config entry creates one diagnostic sensor (entity category `diagnostic`), attached to the target thermostat's device. Its state is `I`; its attributes are `e`, `v_p`, `saturated` and the current on_percent. A thermostat without an entry has no sensor.

## VTherm settings to review

These are configuration, not code, but they change what the valve actually does:

- `auto_regulation_dpercent` (default 10%): smaller openings become 0 and smaller changes are ignored. Recommend about 3%.
- `auto_regulation_period_min` (default 5 min): rate limit on valve commands.
- Min/max opening degrees and opening threshold remap `on_percent` before it reaches the TRV.

## Out of scope (v1)

- A "reset integral" service.
- Trimming SAT's curve from valve positions (the "most-open valve" automation). This lives in HA YAML, outside this plugin.

## Known issues

- VTherm 10.6.3 has two code paths that don't handle `on_percent = None`: the window slope check, and the safety manager on non-over_climate thermostats. They only matter when a temperature is missing while heating, and they affect VTherm's built-in TPI equally. This is upstream and not fixed here.
- Deleting a VTherm thermostat leaves its `tpi_for_sat.<unique_id>` storage file behind.
