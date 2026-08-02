"""Necromancer `You prepare ...` prose flavors -> ability names (EoF era).

Derived from bobby.txt: each mapping verified either by the following
YOUR-ability line ("to rot a soul" -> Soulrot) or by the summoned swarm pet
sharing the spell's name ("Bobby's awaken grave" appears after "You prepare
to awaken the grave"). Article-form flavors ("the Bloodcloud") resolve
generically in flavor/__init__.py and don't belong here — only prose
"to ..." forms do.
"""

FLAVOR_MAP = {
    "to rot a soul": "Soulrot",
    "to unleash the Blighted Horde": "Blighted Horde",
    "to awaken the grave": "Awaken Grave",
    "to unleash a Rending Frenzy": "Rending Frenzy",
}
