"""Presets por edad: aplican un conjunto de reglas sensato con un toque."""
from .models import Rule

PRESETS = {
    "pequeno": {
        "label": "Pequeño (3–7)",
        "block": ["adult", "gambling", "social", "messaging", "news", "evasion"],
    },
    "nino": {
        "label": "Niño (8–12)",
        "block": ["adult", "gambling", "messaging", "evasion"],
    },
    "adolescente": {
        "label": "Adolescente (13–17)",
        "block": ["adult", "gambling", "evasion"],
    },
    "personalizado": {
        "label": "Personalizado",
        "block": [],
    },
}


def apply_preset(db, profile, preset_key):
    """Reemplaza las reglas de categoría del perfil según el preset elegido."""
    preset = PRESETS.get(preset_key, PRESETS["nino"])
    profile.preset = preset_key
    for r in list(profile.rules):
        if r.scope == "category":
            db.delete(r)
    for cat in preset["block"]:
        db.add(Rule(profile_id=profile.id, scope="category", value=cat, action="block"))
    db.commit()
