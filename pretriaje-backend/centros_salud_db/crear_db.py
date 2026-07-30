"""Construye centros_salud.db (SQLite) desde schema.sql + datos/centros.json.

Uso:  python crear_db.py
"""
import json
import sqlite3
import sys
from pathlib import Path

# la consola de Windows no usa UTF-8 por defecto y los nombres llevan tildes
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = Path(__file__).parent
DB = BASE / 'centros_salud.db'
SCHEMA = BASE / 'schema.sql'
DATOS = BASE / 'datos' / 'centros.json'


def main():
    centros = json.loads(DATOS.read_text(encoding='utf-8'))

    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA.read_text(encoding='utf-8'))

    nombres = sorted({e for c in centros for e in c['especialidades']})
    con.executemany('INSERT INTO especialidades (nombre) VALUES (?)',
                    [(n,) for n in nombres])
    esp_id = dict(con.execute('SELECT nombre, id FROM especialidades'))

    for c in centros:
        cur = con.execute(
            '''INSERT INTO centros (nombre, tipo, dependencia, ciudad,
                                    provincia, direccion, barrio, telefono,
                                    horario, fuente)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (c['nombre'], c['tipo'], c['dependencia'], c['ciudad'],
             c['provincia'], c['direccion'], c['barrio'], c['telefono'],
             c['horario'], c['fuente']))
        con.executemany(
            'INSERT INTO centro_especialidad VALUES (?, ?)',
            [(cur.lastrowid, esp_id[e]) for e in c['especialidades']])

    con.commit()

    n_cen, = con.execute('SELECT count(*) FROM centros').fetchone()
    n_esp, = con.execute('SELECT count(*) FROM especialidades').fetchone()
    n_rel, = con.execute('SELECT count(*) FROM centro_especialidad').fetchone()
    print(f'{DB.name}: {n_cen} centros, {n_esp} especialidades, '
          f'{n_rel} relaciones')
    for ciudad, n in con.execute(
            'SELECT ciudad, count(*) FROM centros GROUP BY ciudad '
            'ORDER BY 2 DESC'):
        print(f'  {ciudad}: {n}')
    con.close()


if __name__ == '__main__':
    main()
