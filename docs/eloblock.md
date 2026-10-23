# eloBlock — device notes

Operational notes for the Vaillant **eloBlock** (electric boiler, here a VE 24 =
24 kW) as seen on the eBUS, captured from the live bus (telegram log + the
integration's own decode). These describe observed behaviour, not configurable
options.

## Identity on the bus

- Sits **behind a VR32/V32 bus coupler** at address **0x38**. The coupler
  reports scan ident **`V32`, HW `9802`** — that ident + HW is only the key that
  resolves to this definition; the coupler itself is not modelled.
- Definition: `configs/vaillant/eloblock.json` (our own, Apache-2.0).

## Who commands it

The **system controller (master `0x10`)** drives it via `b510 SetMode`:

- idle: `00 0000ffffff010000`
- DHW charge (seen 06:20): `00 03ffff73ff01c800` → `hcmode = 3` ("water").

The controller commands **only the mode + a temperature demand** — never the
power level.

## Power / modulation (device-internal, not bus-controllable)

- The eloBlock reports its modulation via **`ModulationDesired` (0d2e00, SIN÷10)**
  = **62.5 %** (≈ the configured DHW part-load `PartloadHwcKW` = 15 kW; measured
  ~14 kW). It ran at part-load, **not** the full 24 kW.
- This value is a **persisted device parameter** (installer menu): it was
  identical before and after a run, and **no bus write ever sets it** — there is
  no writable `ModulationDesired` in the Vaillant message set, and `0e2e00` on
  this device is a *different* register (`AlarmOnDelay`/`SwitchOffParty`). So the
  part-load is **not changeable over eBUS**, only at the appliance.
- The eloBlock's **own electrical power is not published on the bus**. The
  `RunDataElectricPowerConsumption` watt register belongs to the **heat pump**
  (≈ 2.3 kW while it contributed), not the eloBlock — take the eloBlock's draw
  from an external meter (~14 kW here).

## DHW charge logic (observed 2026-10-23, setpoint 50 °C)

The eloBlock heats the *flow* above the tank setpoint to charge the DHW tank.
It cooperates with the aroTHERM: the heat pump gives a short low-power
contribution, the eloBlock does the high-temperature finish.

| run | eloBlock flow | tank (T1 top / T2 bottom) |
|---|---|---|
| 06:20–06:41 (recharge) | 30 → ~53 °C, peak **58.8 °C** | T2 **45 → 48 °C**, T1 49 → 50 °C |
| 09:07–09:26 (top-up) | ~46.5 → 50.4 °C | ~48–50 °C, held |

Part-load (~14 kW) is sized for a 50 °C tank — the full 24 kW is not needed.

## Definition details learned from the live run

- `Statenumber` gained `20 = anlauf`: a brief transient (`sommer → anlauf →
  warmwasser`) at charge start.
- `ReturnTemp` (0d9800) was **removed**: the electric boiler has no return
  sensor and the register read a constant 0.
