"""Genera postgres/seed.sql (INSERTs) desde datos/centros.json.

Sólo stdlib: no hace falta ningún driver de PostgreSQL, el archivo se carga
después con psql. Uso:  python postgres/generar_seed.py
"""
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = Path(__file__).resolve().parent.parent
DATOS = BASE / 'datos' / 'centros.json'
SEED = BASE / 'postgres' / 'seed.sql'

CAMPOS = ['nombre', 'tipo', 'dependencia', 'ciudad', 'provincia', 'direccion',
          'barrio', 'telefono', 'horario', 'fuente']


def lit(v):
    if v is None or v == '':
        return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"


def main():
    centros = json.loads(DATOS.read_text(encoding='utf-8'))
    especialidades = sorted({e for c in centros for e in c['especialidades']})
    esp_id = {n: i for i, n in enumerate(especialidades, 1)}

    out = ['-- Generado por postgres/generar_seed.py — no editar a mano.',
           '-- Editar datos/centros.json y volver a generar.',
           'BEGIN;', '']

    out.append('INSERT INTO especialidades (id, nombre) VALUES')
    out.append(',\n'.join(f'  ({i}, {lit(n)})' for n, i in esp_id.items()) + ';')
    out.append('')

    out.append('INSERT INTO centros (id, %s) VALUES' % ', '.join(CAMPOS))
    filas = []
    for i, c in enumerate(centros, 1):
        vals = ', '.join(lit(c[k]) for k in CAMPOS)
        filas.append(f'  ({i}, {vals})')
    out.append(',\n'.join(filas) + ';')
    out.append('')

    pares = [(i, esp_id[e]) for i, c in enumerate(centros, 1)
             for e in c['especialidades']]
    out.append('INSERT INTO centro_especialidad (centro_id, especialidad_id) VALUES')
    out.append(',\n'.join(f'  ({a}, {b})' for a, b in pares) + ';')
    out.append('')

    # los id se insertaron explícitos: hay que adelantar las secuencias.
    # va en un bloque DO para que setval() no imprima filas de resultado.
    out.append('DO $$ BEGIN')
    out.append("  PERFORM setval('centros_id_seq', "
               "(SELECT max(id) FROM centros));")
    out.append("  PERFORM setval('especialidades_id_seq', "
               "(SELECT max(id) FROM especialidades));")
    out.append('END $$;')
    out.append('')
    out.append('COMMIT;')

    SEED.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print(f'{SEED.name}: {len(centros)} centros, {len(especialidades)} '
          f'especialidades, {len(pares)} relaciones')


if __name__ == '__main__':
    main()
