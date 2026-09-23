# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegato: React, React Three Fiber e FastAPI, scelti per separare il rendering mobile dalla simulazione scientifica Python.

## Users

L'utente principale e WhyEd, che dirige esperimenti dal PC e li controlla spesso da iPhone o iPad sulla stessa rete Wi-Fi. Claude Code e Codex devono poter continuare il lavoro leggendo stato, specifiche e risultati senza ricostruire il contesto.

## Product Purpose

Fruit Fly Lab rende osservabile, controllabile, ripetibile e archiviabile la simulazione della Drosophila. Il successo significa poter creare molte campagne, controllare la mosca in tempo reale dal telefono, ritrovare ogni prova e confrontarla senza perdere dati.

## Positioning

La fisica e il connectome restano autorevoli sul PC, mentre il browser e una finestra interattiva leggera. Ogni run conserva configurazione, seed, versione e artefatti verificati, quindi il laboratorio unisce teleoperazione e ricerca riproducibile.

## Operating Context

Il PC Windows esegue FlyGym 2.1.0, MuJoCo e il modello Shiu et al. del cervello intero. Il telefono usa Safari o Chrome in rete locale. Le sessioni possono durare a lungo e passare da Codex a Claude Code.

## Capabilities and Constraints

- Link locale temporaneo con accesso autenticato.
- Vista 3D, controllo live, coda batch, replay e confronto.
- Archivio SQLite, MCAP, Parquet e SHA-256 senza cancellazione automatica.
- Un solo processo possiede MuJoCo.
- Il connectome usa circa 10 GB di RAM e viene collegato in modo asincrono.
- Il disco E ha spazio fisico limitato, quindi code e artefatti devono essere stimati, compressi e deduplicati.
- La prima versione non espone porte su Internet e non parte automaticamente con Windows.

## Brand Commitments

Nome: Fruit Fly Lab. Interfaccia giapponese minimale, vettoriale piatta, colori pieni, linee sottili e masse nere controllate. Nessuna sfumatura, nessun 3D decorativo, nessun testo generico da dashboard AI. Il 3D e funzionale: mostra la mosca e lo spazio dell'esperimento.

## Evidence on Hand

- `prove/01-zucchero.py`: connectome funzionante con 127.400 neuroni.
- `prove/02-cammina.py`: corpo FlyGym/MuJoCo funzionante.
- `docs/superpowers/specs/2026-09-22-fruit-fly-lab-design.md`: specifica approvata.
- Non esistono ancora prove reali da iPhone/iPad per il nuovo server.

## Product Principles

- La validita scientifica viene prima della spettacolarita.
- Ogni esperimento deve essere ripetibile e recuperabile.
- Il telefono controlla senza diventare parte della fisica.
- Nessun dato viene cancellato automaticamente.
- L'interfaccia mostra subito stato, rischio e prossimo gesto.

## Accessibility & Inclusion

Controlli touch di almeno 44 px, testo mobile di almeno 16 px, contrasto WCAG AA, focus visibile, significato non affidato soltanto al colore e rispetto di `prefers-reduced-motion`.

