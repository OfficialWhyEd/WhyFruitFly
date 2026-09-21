# Prova 01: il moscerino assaggia lo zucchero.
# Attiviamo i 21 neuroni del gusto (zucchero, emisfero destro) e guardiamo chi si accende
# nel resto del cervello. Nel moscerino vero questo fa allungare la proboscide (MN9).
# Esce: prove/01-zucchero.png (raster + i 20 neuroni piu' attivi) e prove/01-zucchero.txt
import sys, time
from pathlib import Path

QUI = Path(__file__).resolve().parent
MOD = QUI.parent / 'cervello' / 'modello'
sys.path.insert(0, str(MOD))

from brian2 import Hz, ms, prefs
prefs.codegen.target = 'numpy'   # niente compilatore C++ su questo PC
from model import run_exp, default_params as params
import utils as utl
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

n_run = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5   # il paper ne fa 30; per la prova bastano 5
params['n_run'] = n_run
params['t_run'] = 1000 * ms

config = {
    'path_res':  str(QUI / 'risultati'),
    'path_comp': str(MOD / '2023_03_23_completeness_630_final.csv'),
    'path_con':  str(MOD / '2023_03_23_connectivity_630_final.parquet'),
    'n_proc':    4,
}
neu_sugar = [
    720575940624963786, 720575940630233916, 720575940637568838, 720575940638202345,
    720575940617000768, 720575940630797113, 720575940632889389, 720575940621754367,
    720575940621502051, 720575940640649691, 720575940639332736, 720575940616885538,
    720575940639198653, 720575940620900446, 720575940617937543, 720575940632425919,
    720575940633143833, 720575940612670570, 720575940628853239, 720575940629176663,
    720575940611875570,
]
ID_MN9 = 720575940660219265  # motoneurone della proboscide
nomi = {f: f'zucchero_{i+1}' for i, f in enumerate(neu_sugar)}
nomi[ID_MN9] = 'MN9_proboscide'

(QUI / 'risultati').mkdir(exist_ok=True)
t0 = time.time()
if not (QUI / 'risultati' / 'zucchero.parquet').exists() or '--rifai' in sys.argv:
    run_exp(exp_name='zucchero', neu_exc=neu_sugar, params=params, **config)
durata = time.time() - t0

df_spike = utl.load_exps([str(QUI / 'risultati' / 'zucchero.parquet')])
df_rate, df_std = utl.get_rate(df_spike, t_run=params['t_run'], n_run=n_run, flyid2name=nomi)
df_rate = df_rate.sort_values('zucchero', ascending=False)

n_neuroni = pd.read_csv(config['path_comp']).shape[0]
attivi = (df_rate['zucchero'] > 0).sum()
mn9 = float(df_rate.loc[ID_MN9, 'zucchero']) if ID_MN9 in df_rate.index else (float(df_rate.loc['MN9_proboscide', 'zucchero']) if 'MN9_proboscide' in df_rate.index else 0.0)

righe = [
    f'Cervello: {n_neuroni} neuroni. Prove: {n_run} x 1 s. Tempo di calcolo: {durata:.0f} s.',
    f'Spike totali: {len(df_spike)}. Neuroni che si sono accesi: {attivi}.',
    f'MN9 (motoneurone della proboscide): {mn9:.1f} Hz -> il moscerino allunga la proboscide.',
    '',
    'I 20 neuroni piu attivi (Hz):',
]
for k, v in df_rate['zucchero'].head(20).items():
    righe.append(f'  {nomi.get(k, k)}: {v:.1f}')
(QUI / '01-zucchero.txt').write_text('\n'.join(righe), encoding='utf-8')
print('\n'.join(righe))

# figura: raster di TUTTI i neuroni accesi nella prova 1 (ordinati dal primo spike: si vede la cascata),
# gli input in grigio, MN9 in rosso; a destra i 20 piu' attivi ESCLUSI gli input (cioe' la risposta del cervello)
fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 6.5), gridspec_kw={'width_ratios': [2, 1]})
uno = df_spike[df_spike['trial'] == 0]
primo = uno.groupby('flywire_id')['t'].min().sort_values()
ordine = {f: i for i, f in enumerate(primo.index)}
col = ['#bbbbbb' if f in neu_sugar else ('#d0021b' if f == ID_MN9 else 'black') for f in uno['flywire_id']]
a1.scatter(uno['t'], [ordine[f] for f in uno['flywire_id']], s=1.5, c=col, linewidths=0)
a1.set_xlabel('tempo (s)'); a1.set_ylabel(f'neurone ({len(ordine)} accesi, in ordine di primo spike)')
a1.set_title('Assaggia lo zucchero: 21 neuroni del gusto (grigio) accendono il resto; MN9 in rosso')
a1.set_ylim(-2, len(ordine) + 2)
risposta = df_rate[~df_rate.index.isin(neu_sugar)]['zucchero'].head(20)[::-1]
etichette = ['MN9 proboscide' if k == ID_MN9 else '...' + str(k)[-6:] for k in risposta.index]
colori = ['#d0021b' if k == ID_MN9 else 'black' for k in risposta.index]
a2.barh(etichette, risposta.values, color=colori)
a2.set_xlabel('Hz'); a2.set_title('I 20 piu attivi a valle (non input)')
plt.tight_layout()
plt.savefig(QUI / '01-zucchero.png', dpi=110)
print('figura:', QUI / '01-zucchero.png')
