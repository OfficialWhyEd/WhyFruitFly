# Fruit Fly Lab Design System

## Direzione

Un insettario scientifico giapponese contemporaneo: una grande superficie chiara su cui la mosca e i suoi dati sembrano annotati con precisione. L'interfaccia opera, non vende. Il soggetto resta visibile mentre stato, tempo e controlli occupano i margini.

## Colore

- Carta: `#F0ECE2`
- Carta chiara: `#F8F5ED`
- Inchiostro: `#151512`
- Inchiostro attenuato: `#625F57`
- Linea: `#CBC5B8`
- Vermiglio: `#D4472F`
- Verde stato: `#2F6F52`

Nessuna sfumatura. Il vermiglio segnala l'azione o il punto scientifico attivo, mai decorazione.

## Tipografia

- Manrope Variable per interfaccia e titoli.
- IBM Plex Mono per misure, timestamp, seed e identificatori.
- Quattro livelli: titolo 28-40, sezione 18-20, corpo 15-16, dati 12-13.

## Composizione

- Desktop: scena dominante, rail informativi asimmetrici ai lati.
- Telefono: scena in alto, stato e comandi in una barra inferiore rispettosa della safe area.
- Divisioni con spazio e linee da 1 px, non con card annidate.
- Touch target minimo 48 px.

## Movimento

- La telemetria muove soltanto la mosca e i grafici.
- Gli stati usano transizioni 160-240 ms su opacity e transform.
- Nessuna animazione decorativa continua.
- `prefers-reduced-motion` disattiva interpolazioni non essenziali.

## Stati

- Pronto: inchiostro neutro.
- In esecuzione: verde con testo esplicito.
- Pausa: vermiglio con testo esplicito.
- Errore: vermiglio, causa e recupero nello stesso blocco.
- Disconnesso: la scena resta visibile con ultimo timestamp e comando di riconnessione.

