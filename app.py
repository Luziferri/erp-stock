from flask import Flask, request, jsonify, render_template, send_file
import os
import tempfile
import json
from werkzeug.utils import secure_filename

import database as db
from excel_parser import parse_excel, get_sheet_names

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

db.init_db()

ALLOWED = {'.xlsx', '.xls', '.csv'}

@app.route('/')
def index():
    return render_template('index.html')

# ---------- API: Produtos / Stock ----------
@app.route('/api/products')
def api_products():
    search = request.args.get('search', '').strip()
    stock_filter = request.args.get('stock_filter', 'todos')
    marca = request.args.get('marca', '')
    supplier = request.args.get('supplier', 'todos')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    offset = (page - 1) * per_page
    rows, total = db.get_all_products(search=search, stock_filter=stock_filter, marca_filter=marca, supplier_filter=supplier, limit=per_page, offset=offset)
    return jsonify({"products": rows, "total": total, "page": page, "per_page": per_page})

@app.route('/api/marcas')
def api_marcas():
    supplier = request.args.get('supplier', 'todos')
    return jsonify(db.get_marcas(supplier_filter=supplier))

@app.route('/api/stats')
def api_stats():
    return jsonify(db.get_stats())

@app.route('/api/imports')
def api_imports():
    return jsonify(db.get_imports())

@app.route('/api/imports/<int:import_id>', methods=['DELETE'])
def api_import_delete(import_id):
    ok, msg = db.delete_import(import_id)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"success": True})

@app.route('/api/imports/<int:import_id>', methods=['GET'])
def api_import_detail(import_id):
    data = db.get_import_detail(import_id)
    if not data:
        return jsonify({"error": "Importação não encontrada"}), 404
    return jsonify(data)

@app.route('/api/imports/<int:import_id>/restore', methods=['POST'])
def api_import_restore(import_id):
    ok, msg = db.restore_import_as_current(import_id)
    if not ok:
        return jsonify({"error": msg}), 404
    return jsonify({"success": True})

# ---------- API: Importar ----------
@app.route('/api/import', methods=['POST'])
def api_import():
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum ficheiro enviado"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Nome de ficheiro vazio"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED:
        return jsonify({"error": f"Extensão não suportada: {ext}. Use .xlsx, .xls ou .csv"}), 400

    supplier = request.form.get('supplier', 'TeamBike')
    # Auto-detectar fornecedor pelo nome do ficheiro para evitar erro (ex: Stock_SportMed.xlsx marcado como TeamBike)
    _low = (file.filename or "").lower()
    _detected = None
    if 'sportmed' in _low or 'sport_med' in _low or 'sport-med' in _low or 'stocks' in _low or 'stocksti' in _low:
        _detected = 'SportMed'
    elif 'teambike' in _low or 'team_bike' in _low or 'team-bike' in _low:
        _detected = 'TeamBike'
    if _detected:
        supplier = _detected
    
    # Guardar temporário
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    file.save(tmp.name)
    tmp.close()

    try:
        # CSV?
        if ext == '.csv':
            import pandas as pd
            # tentar detetar encoding
            df = pd.read_csv(tmp.name, dtype=str, sep=None, engine='python')
            # salvar como xlsx temp para parser unificado? Mais simples: criar xlsx temporário
            tmp_xlsx = tmp.name + ".xlsx"
            df.to_excel(tmp_xlsx, index=False)
            rows, mapping, preview, warnings = parse_excel(tmp_xlsx)
            os.unlink(tmp_xlsx)
        else:
            # Verificar múltiplas sheets? Se tiver mais que 1, usar a primeira com dados
            sheets = get_sheet_names(tmp.name)
            # Tenta cada sheet até achar uma com dados válidos
            last_error = None
            rows = mapping = preview = warnings = None
            for sheet in sheets:
                try:
                    r, m, p, w = parse_excel(tmp.name, sheet_name=sheet)
                    if len(r) > 0:
                        rows, mapping, preview, warnings = r, m, p, w
                        break
                except Exception as e:
                    last_error = str(e)
                    continue
            if rows is None:
                raise ValueError(last_error or "Não foi possível ler nenhuma folha do Excel")

        # Criar import
        filename = secure_filename(file.filename)
        import_id = db.create_import(filename, supplier=supplier, total=len(rows))
        db.upsert_products(rows, import_id, supplier=supplier)
        # Remover de products só desse fornecedor o que desapareceu (separação visível)
        refs = [r["referencia"] for r in rows]
        db.delete_missing_products(refs, import_id, supplier=supplier)
        # Atualiza total no import (caso necessário)
        # Stats por fornecedor
        verificacao = db.get_verificacao(supplier_filter=supplier)

        return jsonify({
            "success": True,
            "import_id": import_id,
            "filename": filename,
            "total": len(rows),
            "mapping": mapping,
            "warnings": warnings,
            "preview": preview[:3],
            "verificacao": verificacao["totais"] if verificacao["tem_comparacao"] else None,
            "tem_comparacao": verificacao["tem_comparacao"]
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp.name)
        except:
            pass

@app.route('/api/preview', methods=['POST'])
def api_preview():
    """Apenas preview sem importar"""
    if 'file' not in request.files:
        return jsonify({"error": "Nenhum ficheiro"}), 400
    file = request.files['file']
    ext = os.path.splitext(file.filename)[1].lower()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    file.save(tmp.name)
    tmp.close()
    try:
        sheets = get_sheet_names(tmp.name)
        rows, mapping, preview, warnings = parse_excel(tmp.name, sheet_name=sheets[0] if sheets else 0)
        return jsonify({"mapping": mapping, "preview": preview, "warnings": warnings, "total": len(rows), "sheets": sheets})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: os.unlink(tmp.name)
        except: pass

# ---------- API: Verificação ----------
@app.route('/api/verificacao')
def api_verificacao():
    supplier = request.args.get('supplier', None)
    data = db.get_verificacao(supplier_filter=supplier)
    return jsonify(data)

@app.route('/api/history')
def api_history():
    conn = db.get_conn()
    imports = [dict(r) for r in conn.execute("SELECT * FROM imports ORDER BY datetime(imported_at) ASC").fetchall()]
    for imp in imports:
        row = conn.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(stock),0) as sum_stock FROM import_products WHERE import_id=?", (imp['id'],)).fetchone()
        imp['product_count'] = row['cnt']
        imp['stock_sum'] = row['sum_stock']
    conn.close()
    return jsonify(imports)

@app.route('/api/product_history/<ref>')
def api_product_history(ref):
    conn = db.get_conn()
    rows = conn.execute("""
        SELECT ip.stock, ip.preco, ip.supplier, ip.marca, i.filename, i.imported_at, i.supplier as imp_supplier
        FROM import_products ip
        JOIN imports i ON i.id = ip.import_id
        WHERE ip.referencia = ?
        ORDER BY datetime(i.imported_at) ASC
    """, (ref,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

# ---------- API: Monitorizados ----------
@app.route('/api/monitored', methods=['GET'])
def api_monitored_get():
    rows = db.get_monitored_fixed()
    return jsonify(rows)

@app.route('/api/monitored', methods=['POST'])
def api_monitored_add():
    data = request.get_json()
    ref = data.get('referencia', '').strip()
    limite = data.get('limite')
    if not ref:
        return jsonify({"error": "Referência vazia"}), 400
    # verificar se produto existe
    try:
        lim = int(limite) if limite is not None else None
    except:
        lim = 0
    db.add_monitored(ref, lim)
    return jsonify({"success": True})

@app.route('/api/monitored/<ref>', methods=['DELETE'])
def api_monitored_del(ref):
    db.remove_monitored(ref)
    return jsonify({"success": True})

@app.route('/api/monitored/<ref>', methods=['PUT'])
def api_monitored_update(ref):
    data = request.get_json()
    limite = data.get('limite')
    try:
        lim = int(limite)
    except:
        return jsonify({"error": "Limite inválido"}), 400
    db.update_monitored_limite(ref, lim)
    return jsonify({"success": True})

# ---------- API: Definições ----------
@app.route('/api/settings', methods=['GET'])
def api_settings_get():
    limite = db.get_setting('limite_padrao', '0')
    supplier = db.get_setting('supplier_default', 'TeamBike')
    return jsonify({"limite_padrao": limite, "supplier_default": supplier})

@app.route('/api/settings', methods=['POST'])
def api_settings_post():
    data = request.get_json()
    if 'limite_padrao' in data:
        db.set_setting('limite_padrao', str(int(data['limite_padrao'])))
    if 'supplier_default' in data:
        db.set_setting('supplier_default', data['supplier_default'])
    return jsonify({"success": True})

@app.route('/api/clear', methods=['POST'])
def api_clear():
    data = request.get_json() or {}
    tipo = data.get('tipo', 'tudo')
    if tipo == 'tudo':
        db.clear_all_data()
        db.clear_monitored()
    elif tipo == 'monitorizados':
        db.clear_monitored()
    return jsonify({"success": True})

@app.route('/api/export')
def api_export():
    """Exporta stock atual para Excel (com separação por fornecedor)"""
    import pandas as pd
    import io
    supplier = request.args.get('supplier', 'todos')
    conn = db.get_conn()
    if supplier and supplier != 'todos':
        rows = conn.execute("SELECT referencia, descricao, stock, preco, marca, categoria, ean, supplier FROM products WHERE supplier=? ORDER BY supplier, referencia", (supplier,)).fetchall()
        filename = f"stock_atual_{supplier}.xlsx"
    else:
        rows = conn.execute("SELECT referencia, descricao, stock, preco, marca, categoria, ean, supplier FROM products ORDER BY supplier, referencia").fetchall()
        filename = "stock_atual.xlsx"
    conn.close()
    if not rows:
        return jsonify({"error": "Sem dados"}), 400
    df = pd.DataFrame([dict(r) for r in rows])
    output = io.BytesIO()
    df.to_excel(output, index=False, engine='openpyxl')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route('/api/backup')
def api_backup():
    """Download backup completo - ZIP com Excel multi-sheet, JSON e DB"""
    import pandas as pd
    import io
    import zipfile
    from datetime import datetime

    conn = db.get_conn()
    products = [dict(r) for r in conn.execute("SELECT referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier FROM products ORDER BY supplier, referencia").fetchall()]
    imports = [dict(r) for r in conn.execute("SELECT * FROM imports ORDER BY id").fetchall()]
    import_products = [dict(r) for r in conn.execute("SELECT import_id, referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier FROM import_products ORDER BY import_id, referencia").fetchall()]
    monitored = [dict(r) for r in conn.execute("SELECT m.referencia, m.limite, m.created_at, p.descricao, p.stock, p.marca FROM monitored m LEFT JOIN products p ON p.referencia=m.referencia ORDER BY m.created_at DESC").fetchall()]
    settings = [dict(r) for r in conn.execute("SELECT * FROM settings").fetchall()]
    conn.close()

    # Excel multi-sheet
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        # Mesmo vazio cria sheet vazia com headers
        pd.DataFrame(products).to_excel(writer, sheet_name='Produtos', index=False)
        pd.DataFrame(imports).to_excel(writer, sheet_name='Importacoes', index=False)
        pd.DataFrame(import_products).to_excel(writer, sheet_name='Import_Produtos', index=False)
        pd.DataFrame(monitored).to_excel(writer, sheet_name='Monitorizados', index=False)
        pd.DataFrame(settings).to_excel(writer, sheet_name='Definicoes', index=False)
    excel_buffer.seek(0)

    dump = {
        "generated_at": datetime.now().isoformat(),
        "products": products,
        "imports": imports,
        "import_products": import_products,
        "monitored": monitored,
        "settings": {r["key"]: r["value"] for r in settings}
    }
    json_bytes = json.dumps(dump, ensure_ascii=False, indent=2).encode('utf-8')

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('backup_completo.xlsx', excel_buffer.getvalue())
        zf.writestr('backup.json', json_bytes)
        if products:
            csv_buf = io.StringIO()
            pd.DataFrame(products).to_csv(csv_buf, index=False)
            zf.writestr('produtos.csv', csv_buf.getvalue())
        # Incluir DB
        try:
            if os.path.exists(db.DB_PATH):
                with open(db.DB_PATH, 'rb') as f:
                    zf.writestr('erp.db', f.read())
        except Exception:
            pass
        zf.writestr('README.txt', 'Backup ERP Stock - TeamBike / SportMed\nGerado em: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '\nConteudo:\n- backup_completo.xlsx (5 sheets: Produtos, Importacoes, Import_Produtos, Monitorizados, Definicoes)\n- backup.json (dump completo em JSON)\n- produtos.csv\n- erp.db (base SQLite)\n')

    mem_zip.seek(0)
    filename = f"backup_erp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(mem_zip, as_attachment=True, download_name=filename, mimetype='application/zip')

@app.route('/api/restore', methods=['POST'])
def api_restore():
    """Restaura backup a partir de ZIP (backup.json) ou JSON direto. Substitui todos os dados."""
    import io
    import zipfile
    import json
    import sqlite3

    if 'file' not in request.files:
        return jsonify({"error": "Nenhum ficheiro enviado"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "Nome de ficheiro vazio"}), 400

    filename = file.filename.lower()
    data_bytes = file.read()
    if not data_bytes:
        return jsonify({"error": "Ficheiro vazio"}), 400

    dump = None
    # Tentar ZIP
    if filename.endswith('.zip') or data_bytes[:4] == b'PK\x03\x04':
        try:
            zf = zipfile.ZipFile(io.BytesIO(data_bytes))
            # Procurar backup.json
            names = zf.namelist()
            json_name = None
            if 'backup.json' in names:
                json_name = 'backup.json'
            else:
                # procurar qualquer .json
                for n in names:
                    if n.lower().endswith('.json'):
                        json_name = n
                        break
            if json_name:
                dump = json.loads(zf.read(json_name).decode('utf-8'))
            else:
                # Se não tem JSON, tentar usar erp.db diretamente (substituir ficheiro)
                if 'erp.db' in names:
                    # Extrair erp.db e substituir
                    db_bytes = zf.read('erp.db')
                    # Fechar conexões e substituir ficheiro
                    tmp_path = db.DB_PATH + ".tmp"
                    with open(tmp_path, 'wb') as out:
                        out.write(db_bytes)
                    # Validar que é SQLite válido
                    try:
                        test_conn = sqlite3.connect(tmp_path)
                        test_conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
                        test_conn.close()
                    except Exception as e:
                        os.unlink(tmp_path)
                        return jsonify({"error": f"erp.db inválido no ZIP: {e}"}), 400
                    # Substituir
                    import shutil
                    # Garantir que não há ligações abertas (get_conn cria novas)
                    os.replace(tmp_path, db.DB_PATH)
                    db.init_db()
                    return jsonify({"success": True, "restored": "erp.db", "imports": len(dump) if dump else 0})
                return jsonify({"error": "ZIP sem backup.json nem erp.db"}), 400
        except zipfile.BadZipFile:
            # Não é ZIP válido, tentar como JSON
            pass
        except Exception as e:
            return jsonify({"error": f"Erro ao ler ZIP: {e}"}), 400

    # Se ainda não temos dump, tentar JSON direto
    if dump is None:
        try:
            text = data_bytes.decode('utf-8')
            # Pode ser JSON com BOM
            if text.startswith('\ufeff'):
                text = text.lstrip('\ufeff')
            dump = json.loads(text)
        except Exception as e:
            return jsonify({"error": f"Ficheiro não é ZIP nem JSON válido: {e}"}), 400

    # Validar estrutura
    if not isinstance(dump, dict):
        return jsonify({"error": "JSON inválido: esperado objeto"}), 400

    # Extrair listas (suporta tanto formato dump com products/imports/import_products/monitored/settings)
    products = dump.get("products") or []
    imports = dump.get("imports") or []
    import_products = dump.get("import_products") or []
    monitored = dump.get("monitored") or []
    settings = dump.get("settings") or {}

    # settings pode ser dict {key: value} ou lista [{key, value}]
    settings_dict = {}
    if isinstance(settings, dict):
        settings_dict = settings
    elif isinstance(settings, list):
        for s in settings:
            if isinstance(s, dict) and "key" in s and "value" in s:
                settings_dict[s["key"]] = s["value"]

    try:
        conn = db.get_conn()
        c = conn.cursor()
        # Limpar tudo (dentro de transação)
        c.execute("DELETE FROM import_products")
        c.execute("DELETE FROM imports")
        c.execute("DELETE FROM products")
        c.execute("DELETE FROM monitored")
        # Não apagar settings ainda, vamos fazer REPLACE depois
        # Restaurar imports com IDs originais
        for imp in imports:
            # Campos esperados: id, filename, imported_at, total_products, supplier
            try:
                c.execute("INSERT INTO imports(id, filename, imported_at, total_products, supplier) VALUES(?,?,?,?,?)",
                          (imp.get("id"), imp.get("filename"), imp.get("imported_at"), imp.get("total_products", 0), imp.get("supplier", "TeamBike")))
            except Exception:
                # Se falhar por ID, tenta sem ID
                c.execute("INSERT INTO imports(filename, imported_at, total_products, supplier) VALUES(?,?,?,?)",
                          (imp.get("filename"), imp.get("imported_at"), imp.get("total_products", 0), imp.get("supplier", "TeamBike")))
        # Restaurar products
        for p in products:
            extra = p.get("extra_json")
            if isinstance(extra, dict):
                extra = json.dumps(extra, ensure_ascii=False)
            # products pode vir do backup com supplier
            c.execute("""INSERT OR REPLACE INTO products(referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier)
                         VALUES(?,?,?,?,?,?,?,?,?)""",
                      (p.get("referencia"), p.get("descricao",""), p.get("stock",0), p.get("preco",0), p.get("marca",""), p.get("categoria",""), p.get("ean",""), extra, p.get("supplier","TeamBike")))
        # Restaurar import_products
        for ip in import_products:
            extra = ip.get("extra_json")
            if isinstance(extra, dict):
                extra = json.dumps(extra, ensure_ascii=False)
            c.execute("""INSERT INTO import_products(import_id, referencia, descricao, stock, preco, marca, categoria, ean, extra_json, supplier)
                         VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      (ip.get("import_id"), ip.get("referencia"), ip.get("descricao",""), ip.get("stock",0), ip.get("preco",0), ip.get("marca",""), ip.get("categoria",""), ip.get("ean",""), extra, ip.get("supplier","TeamBike")))
        # Restaurar monitored
        for m in monitored:
            # monitored no backup pode ter estrutura diferente (ref_monitor, referencia, limite, etc)
            ref = m.get("referencia") or m.get("ref_monitor")
            if not ref:
                continue
            limite = m.get("limite", 0)
            created = m.get("created_at") or m.get("created_at") or __import__('datetime').datetime.now().isoformat()
            try:
                c.execute("INSERT OR REPLACE INTO monitored(referencia, limite, created_at) VALUES(?,?,?)", (ref, int(limite), created))
            except:
                c.execute("INSERT OR REPLACE INTO monitored(referencia, limite, created_at) VALUES(?,?,?)", (ref, 0, created))
        # Restaurar settings
        for k, v in settings_dict.items():
            c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (k, str(v)))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "restored": {"imports": len(imports), "products": len(products), "import_products": len(import_products), "monitorizados": len(monitored)}})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Erro ao restaurar: {e}"}), 500

if __name__ == '__main__':
    import sys, os
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 5001))
    print("="*60)
    print("  ERP Stock - TeamBike / SportMed")
    print(f"  A correr em: http://localhost:{port}")
    print("  Prima CTRL+C para parar")
    print("="*60)
    app.run(host='0.0.0.0', port=port, debug=False)
