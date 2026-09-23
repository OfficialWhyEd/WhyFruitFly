# Fruit Fly Lab

**Run the whole fruit fly brain (FlyWire connectome, ~127k neurons) and body (NeuroMechFly) on a home PC, with a LAN server and a 3D web interface you can open from a phone.**

Esperimento: far girare in locale il cervello e il corpo del moscerino della frutta, e prenderne il principio
(stimolo, stato interno, scelta con rumore, movimento) per il comportamento della mascotte Aphelios.

## Cosa c'è
| Parte | Cosa fa |
|---|---|
| `prove/01-zucchero.py` | stimola i neuroni del gusto: 127.400 neuroni, 5 prove da 1 s in 76 s su CPU |
| `prove/02-cammina.py` | il corpo in fisica MuJoCo che cammina |
| `lab/backend/` | motore a proprietario unico, controller, archivio delle sessioni, server LAN con token (Python, con test) |
| `lab/frontend/` | interfaccia 3D (Vite + React + TypeScript) per vedere e comandare il moscerino dal telefono |
| `docs/connectome/` | piano e adattatore per collegare il connectome al comportamento |

## Da dove viene
- Modello del cervello: Shiu et al. 2024, *Nature*, [philshiu/Drosophila_brain_model](https://github.com/philshiu/Drosophila_brain_model) (va scaricato a parte)
- Corpo: [NeuroMechFly / flygym](https://github.com/NeLy-EPFL/flygym), EPFL

## Stato
Fasi 1 e 2 fatte (motore e server LAN con interfaccia 3D), costruite a quattro mani con Codex.
Da fare: archivio immutabile delle sessioni, prova vera da iPhone e iPad, campagne di esperimenti, connectome collegato ad Aphelios.

---

Parte di **[WhyEcosystem 2023-2026](https://github.com/OfficialWhyEd/WhyEcosystem-2023-2026)**: il percorso di WhyEd, producer e sound engineer che costruisce sistemi AI dirigendo gli agenti.  
Costruito da WhyEd con Claude Code
