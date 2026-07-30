"""Consultas sobre centros_salud.db.

  python consultar.py especialidades
  python consultar.py ciudades
  python consultar.py buscar "pediatr"                 # por especialidad
  python consultar.py buscar "pediatr" --ciudad Paraná
  python consultar.py centro "candioti"                # detalle de un centro
  python consultar.py listar --ciudad "Santa Fe" --tipo hospital
"""
import argparse
import sqlite3
import sys
import unicodedata
from pathlib import Path

DB = Path(__file__).parent / 'centros_salud.db'

# la consola de Windows no usa UTF-8 por defecto y los nombres llevan tildes
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def sin_acento(s):
    if s is None:
        return None
    return ''.join(c for c in unicodedata.normalize('NFD', s.lower())
                   if not unicodedata.combining(c))


def conectar():
    if not DB.exists():
        sys.exit('Falta centros_salud.db — corré primero: python crear_db.py')
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.create_function('sin_acento', 1, sin_acento)
    return con


def tabla(filas, columnas):
    if not filas:
        print('Sin resultados.')
        return
    anchos = [max(len(c), max(len(str(f[c] or '-')) for f in filas))
              for c in columnas]
    print('  '.join(c.upper().ljust(a) for c, a in zip(columnas, anchos)))
    print('  '.join('-' * a for a in anchos))
    for f in filas:
        print('  '.join(str(f[c] or '-').ljust(a)
                        for c, a in zip(columnas, anchos)))
    print(f'\n{len(filas)} fila(s).')


def cmd_especialidades(con, args):
    filas = con.execute('''
        SELECT e.nombre AS especialidad, count(ce.centro_id) AS centros
        FROM especialidades e
        LEFT JOIN centro_especialidad ce ON ce.especialidad_id = e.id
        GROUP BY e.id ORDER BY centros DESC, e.nombre''').fetchall()
    tabla(filas, ['especialidad', 'centros'])


def cmd_ciudades(con, args):
    filas = con.execute('''
        SELECT ciudad, provincia, count(*) AS centros
        FROM centros GROUP BY ciudad, provincia ORDER BY centros DESC''')
    tabla(filas.fetchall(), ['ciudad', 'provincia', 'centros'])


def cmd_buscar(con, args):
    sql = '''SELECT centro, especialidad, ciudad, direccion, barrio, telefono
             FROM vw_centros_especialidades
             WHERE sin_acento(especialidad) LIKE ?'''
    params = [f'%{sin_acento(args.texto)}%']
    if args.ciudad:
        sql += ' AND sin_acento(ciudad) = ?'
        params.append(sin_acento(args.ciudad))
    sql += ' ORDER BY ciudad, centro'
    tabla(con.execute(sql, params).fetchall(),
          ['centro', 'especialidad', 'ciudad', 'direccion', 'telefono'])


def cmd_listar(con, args):
    sql = 'SELECT nombre, tipo, ciudad, direccion, barrio, telefono FROM centros WHERE 1=1'
    params = []
    if args.ciudad:
        sql += ' AND sin_acento(ciudad) = ?'
        params.append(sin_acento(args.ciudad))
    if args.tipo:
        sql += ' AND sin_acento(tipo) = ?'
        params.append(sin_acento(args.tipo))
    sql += ' ORDER BY ciudad, nombre'
    tabla(con.execute(sql, params).fetchall(),
          ['nombre', 'tipo', 'ciudad', 'direccion', 'barrio', 'telefono'])


def cmd_centro(con, args):
    filas = con.execute(
        'SELECT * FROM centros WHERE sin_acento(nombre) LIKE ? ORDER BY nombre',
        (f'%{sin_acento(args.texto)}%',)).fetchall()
    if not filas:
        print('Sin resultados.')
        return
    for c in filas:
        esp = [r[0] for r in con.execute('''
            SELECT e.nombre FROM especialidades e
            JOIN centro_especialidad ce ON ce.especialidad_id = e.id
            WHERE ce.centro_id = ? ORDER BY e.nombre''', (c['id'],))]
        print(f"\n{c['nombre']}  [{c['tipo']} / {c['dependencia']}]")
        print(f"  Ubicación : {c['direccion']}, {c['ciudad']}, {c['provincia']}")
        if c['barrio']:
            print(f"  Barrio    : {c['barrio']}")
        if c['telefono']:
            print(f"  Teléfono  : {c['telefono']}")
        if c['horario']:
            print(f"  Horario   : {c['horario']}")
        print(f"  Especialidades ({len(esp)}): "
              + (', '.join(esp) if esp else 'no publicadas'))
        print(f"  Fuente    : {c['fuente']}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('especialidades', help='especialidades y cuántos centros las ofrecen')
    sub.add_parser('ciudades', help='centros por ciudad')

    b = sub.add_parser('buscar', help='centros que ofrecen una especialidad')
    b.add_argument('texto')
    b.add_argument('--ciudad')

    l = sub.add_parser('listar', help='listar centros')
    l.add_argument('--ciudad')
    l.add_argument('--tipo')

    c = sub.add_parser('centro', help='detalle de un centro')
    c.add_argument('texto')

    args = p.parse_args()
    con = conectar()
    try:
        globals()['cmd_' + args.cmd](con, args)
    finally:
        con.close()


if __name__ == '__main__':
    main()
