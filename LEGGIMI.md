# Fruit fly: il connectome in locale

Nato il 15/09/2026 con "Aphase 1". Esperimento: far girare sul PC il cervello (e il
corpo) del moscerino della frutta, e poi usarlo per il comportamento di Aphi.

## Cosa c'e'
- `cervello/modello/`  il modello leaky integrate-and-fire dell'intero cervello
  (Shiu et al. 2024, Nature; 127k neuroni, connectome FlyWire v630 e v783).
  Repo: github.com/philshiu/Drosophila_brain_model. Gira in Brian2 su CPU.
- `corpo/`             NeuroMechFly (flygym, EPFL): il corpo del moscerino in fisica
  MuJoCo, cammina con generatori di passo. Serve per rig e animazioni.
- `prove/`             risultati: grafici, video, note. Un file per prova.
- `venv/`              ambiente Python (3.12) con brian2, flygym e il resto.

## Come si lancia
    E:/Dev/FruitFly/venv/Scripts/python prove/01-zucchero.py
La prima volta Brian2 costruisce la rete: alcuni minuti e diversi GB di RAM.

## Perche' ci interessa per Aphi
Non per copiarlo: per prendere il PRINCIPIO. Un cervello vero e' stocastico, non
deterministico; risponde a stimoli con sequenze di attivita' che diventano comportamento
(assaggia zucchero -> allunga la proboscide). Aphi deve muoversi cosi': stimolo, stato
interno, scelta con rumore, movimento. Il ponte sta in `../Mascotte/rig/aphi-core.js`
(`AphiBrain`).

## Stato al 15/09/2026, 23:25
- `prove/01-zucchero.py`: FUNZIONA. 127.400 neuroni, 5 prove da 1 s in 76 s (4 processi,
  ~10 GB di RAM, Brian2 in modalita' numpy: niente compilatore C++ sul PC). Zucchero ->
  cascata di 390 neuroni -> MN9 (proboscide) a 80 Hz. Figura `01-zucchero.png`.
  Rilanciare la simulazione: `--rifai`; senza, rilegge `risultati/zucchero.parquet`.
- `prove/02-cammina.py`: FUNZIONA. flygym 2.1.0 (API nuova, MjSpec: niente controllori
  pronti dentro il pacchetto). Camminata a tripode scritta a mano (CPG sinusoidale),
  lenta (0,8 mm/s contro i 10-20 di un moscerino vero). `MUJOCO_GL=glfw` e' l'unico
  backend che funziona su Windows. Video `02-cammina.mp4`, fotogramma `02-cammina.png`.
- Prossimo: collegare i due (spike dei motoneuroni -> zampe) e' il lavoro di ricerca vero;
  per Aphi ci serve il PRINCIPIO (vedi sopra), non il collegamento completo.
