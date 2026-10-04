"""Dimensional unit conversion without concentration-basis imputation."""

import pint

from livingmeta.domain import Measurement

UNITS = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
ALIASES = {"μm": "micrometer", "µm": "micrometer", "um": "micrometer", "nm": "nanometer",
           "mM": "millimole/liter", "mV": "millivolt", "deg": "degree", "°": "degree",
           "°C": "degC", "Celsius": "degC", "days": "day", "wt%": "percent", "vol%": "percent",
           "wt.%": "percent", "vol.%": "percent", "wt %": "percent", "vol %": "percent",
           "mmol/g": "millimole/gram", "mJ/m2": "millijoule/meter**2", "e/nm2": "elementary_charge/nanometer**2"}
TARGETS = {"Droplet_Size_um": "micrometer", "Particle_L_nm": "nanometer", "Particle_w_nm": "nanometer",
           "Stability_Days": "day", "Storage_Time": "day", "Zeta_Potential_mV": "millivolt",
           "Electrolyte_Concentration_mM": "millimole/liter", "Contact_Angle_deg": "degree",
           "Surface_Charge_Density_mmol_g": "millimole/gram", "Surface_Energy_mJ_m2": "millijoule/meter**2",
           "Temperature": "degC"}


def parse_unit(unit: str):
    cleaned = unit.strip().replace("−", "-").replace("²", "**2").replace("³", "**3")
    return UNITS.parse_units(ALIASES.get(cleaned, cleaned))


def compatible_length_unit(first: str, second: str) -> bool:
    try:
        a, b = parse_unit(first), parse_unit(second)
        return a == b and a.dimensionality == UNITS.meter.dimensionality
    except (pint.UndefinedUnitError, ValueError, TypeError):
        return False


def normalize_measurement(measurement: Measurement) -> Measurement:
    output = measurement.model_copy(deep=True)
    if output.value is None or not output.unit:
        return output
    # Do not assign a basis from an outcome label alone.
    concentration = any(term in output.outcome.lower() for term in ("concentration", "content", "fraction"))
    percentage = "%" in output.unit or "percent" in output.unit
    if concentration and percentage and not output.concentration_basis:
        output.validation_notes.append("Concentration basis is unavailable; conversion withheld")
        return output
    if concentration and percentage:
        unit = output.unit.lower()
        declared = output.concentration_basis.lower()
        if (("wt" in unit and declared not in {"mass", "mass/mass", "w/w", "wt", "weight"})
                or ("vol" in unit and declared not in {"volume", "volume/volume", "v/v", "vol"})):
            output.validation_notes.append("Printed concentration unit disagrees with declared basis")
            output.status = "uncertain"
            return output
    try:
        unit = parse_unit(output.unit)
        target = TARGETS.get(output.outcome)
        quantity = UNITS.Quantity(output.value, unit)
        converted = quantity.to(target) if target else quantity
        output.normalized_value = float(converted.magnitude)
        output.normalized_unit = str(converted.units)
        output.validation_notes.append(f"Pint conversion: {output.unit} -> {output.normalized_unit}; original value retained")
    except (pint.UndefinedUnitError, pint.DimensionalityError, ValueError, TypeError):
        output.validation_notes.append("Unit is unsupported or incompatible; normalization withheld")
    return output
