import os, sqlite3, datetime, requests, threading, time, re
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from database import init_db

app = Flask(__name__)
# SEGURANÇA MÁXIMA: Chave dinâmica se não houver variável de ambiente
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
init_db()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- AUTH ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u, p = request.form['username'], request.form['password']
        conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor()
        c.execute("SELECT id, password_hash FROM users WHERE username = ?", (u,))
        row = c.fetchone(); conn.close()
        if row and check_password_hash(row[1], p):
            session['user_id'], session['username'] = row[0], u
            return redirect(url_for('index'))
        return render_template('login.html', erro='Credenciais inválidas.')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u, p = request.form['username'], request.form['password']
        hash_p = generate_password_hash(p)
        conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)", (u, hash_p, datetime.date.today().strftime("%Y-%m-%d")))
            uid = c.lastrowid
            c.executemany("INSERT INTO plataformas (nome, taxa, user_id) VALUES (?, ?, ?)", [('Steam', 15.0, uid), ('CSFloat', 2.0, uid)])
            c.executemany("INSERT INTO categorias (nome, user_id) VALUES (?, ?)", [('Giro', uid), ('Hold', uid)])
            c.executemany("INSERT INTO tesouraria (chave, valor, user_id) VALUES (?, ?, ?)", [('passes_arsenal', 0, uid), ('estrelas', 0, uid)])
            conn.commit(); session['user_id'], session['username'] = uid, u
            return redirect(url_for('index'))
        except: return render_template('register.html', erro='Usuário já existe.')
        finally: conn.close()
    return render_template('register.html')

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

# --- MOTOR DE SYNC ---
sync_state = {'is_syncing': False, 'total': 0, 'current': 0, 'current_item': ''}
def background_sync_task(uid):
    global sync_state
    sync_state['is_syncing'] = True
    try:
        conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor()
        c.execute("SELECT id, nome_item FROM inventory WHERE status = 'Ativo' AND user_id = ?", (uid,))
        rows = c.fetchall(); grouped = {}
        for r in rows:
            n = r[1].strip()
            if n not in grouped: grouped[n] = []
            grouped[n].append(r[0])
        items = [{"hash_name": k, "ids": v} for k, v in grouped.items()]
        sync_state['total'] = len(items)
        for i, it in enumerate(items):
            sync_state['current'], sync_state['current_item'] = i + 1, it['hash_name']
            url = f"https://steamcommunity.com/market/priceoverview/?appid=730&currency=1&market_hash_name={it['hash_name']}"
            try:
                res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
                if res.status_code == 200:
                    d = res.json()
                    if d.get('success'):
                        price = float((d.get('lowest_price') or d.get('median_price', '0')).replace('$', '').replace(',', '').strip())
                        ph = ','.join('?' for _ in it['ids'])
                        c.execute(f"UPDATE inventory SET preco_mercado = ? WHERE id IN ({ph}) AND user_id = ?", [price] + it['ids'] + [uid])
                        conn.commit()
                elif res.status_code == 429: time.sleep(15)
            except: pass
            if i < len(items) - 1: time.sleep(4)
        conn.close()
    finally: sync_state['is_syncing'] = False

# --- VIEWS ---
def get_view_data(tab, uid):
    conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor()
    c.execute("SELECT id, nome, taxa FROM plataformas WHERE user_id = ?", (uid,)); plats = [{'id': r[0], 'nome': r[1], 'taxa': r[2]} for r in c.fetchall()]
    c.execute("SELECT id, nome FROM categorias WHERE user_id = ?", (uid,)); cats = [{'id': r[0], 'nome': r[1]} for r in c.fetchall()]
    c.execute("SELECT id, nome FROM sites_bets WHERE user_id = ?", (uid,)); bets = [{'id': r[0], 'nome': r[1]} for r in c.fetchall()]
    c.execute("SELECT chave, valor FROM tesouraria WHERE user_id = ?", (uid,)); treasury = dict(c.fetchall())
    
    c.execute("SELECT categoria, SUM(CASE WHEN preco_mercado > 0 THEN preco_mercado ELSE preco_compra END) FROM inventory WHERE status='Ativo' AND user_id = ? GROUP BY categoria", (uid,))
    cat_saldos = c.fetchall(); total_caixa = sum(r[1] for r in cat_saldos)
    chart_cat_labels = [r[0] for r in cat_saldos]; chart_cat_data = [round(r[1], 2) for r in cat_saldos]
    
    c.execute("SELECT plataforma, SUM(CASE WHEN preco_mercado > 0 THEN preco_mercado ELSE preco_compra END) FROM inventory WHERE status='Ativo' AND user_id = ? GROUP BY plataforma", (uid,))
    plat_saldos = c.fetchall()
    chart_plat_labels = [r[0] for r in plat_saldos]; chart_plat_data = [round(r[1], 2) for r in plat_saldos]

    c.execute("SELECT preco_compra, preco_venda FROM inventory WHERE status = 'Vendido' AND user_id = ?", (uid,))
    vendas = c.fetchall(); tp = sum(v[1] - v[0] for v in vendas); tc = sum(v[0] for v in vendas); tr = (tp/tc*100) if tc > 0 else 0
    c.execute("SELECT COUNT(*) FROM inventory WHERE status='Ativo' AND user_id = ?", (uid,)); count_ativos = c.fetchone()[0]
    
    c.execute("SELECT nome_item, (preco_venda - preco_compra) as lucro FROM inventory WHERE status='Vendido' AND user_id = ? ORDER BY lucro DESC LIMIT 5", (uid,))
    top_trades = c.fetchall()
    chart_trade_labels = [r[0][:15]+"..." for r in top_trades]; chart_trade_data = [round(r[1], 2) for r in top_trades]

    nav_links = [{'url': '/', 'label': '🚀 Dashboard Central', 'id': 'dashboard', 'css_class': ''}]
    for cat in cats:
        n_c = cat['nome'].lower().replace(" ", "")
        nav_links.append({'url': f'/cat_{n_c}', 'label': f'📌 {cat["nome"]}', 'id': f'cat_{n_c}', 'db_name': cat['nome'], 'css_class': ''})
    nav_links.extend([
        {'url': '/import_steam', 'label': '📥 Importar Steam', 'id': 'import_steam', 'extra': 'mt-4 border border-blue-700 bg-gray-900 text-blue-400'},
        {'url': '/storage', 'label': '📦 Storage Units', 'id': 'storage', 'extra': 'mt-2 border border-gray-700'},
        {'url': '/bets', 'label': '🎲 Bets', 'id': 'bets', 'extra': 'mt-2 border border-gray-700 bg-gray-900 text-yellow-500'},
        {'url': '/historico', 'label': '📜 Histórico Trades', 'id': 'historico', 'extra': 'mt-2 border border-gray-700'},
        {'url': '/simulador', 'label': '🧮 Simulador Flip', 'id': 'simulador', 'extra': 'mt-2 border border-gray-700'},
        {'url': '/analytics', 'label': '📊 Analytics', 'id': 'analytics', 'extra': 'mt-2 font-bold border border-emerald-900 text-emerald-400'},
        {'url': '/admin', 'label': '⚙️ Administração', 'id': 'admin', 'extra': 'mt-2'}
    ])

    active_tab_name = ""
    for nav in nav_links:
        base = 'bg-gray-800 text-white' if tab == nav['id'] else 'text-gray-400 hover:bg-gray-800'
        nav['css_class'] = base + ' ' + nav.get('extra', '')
        if tab == nav['id'] and 'db_name' in nav: active_tab_name = nav['db_name']

    hoje = datetime.date.today(); items = []; items_container = []; items_hist = []
    if tab == 'historico':
        c.execute("SELECT id, nome_item, categoria, plataforma, preco_compra, data_compra, preco_venda, data_venda, plataforma_venda FROM inventory WHERE status = 'Vendido' AND user_id = ? ORDER BY data_venda DESC, id DESC", (uid,))
        for r in c.fetchall():
            lc = r[6]-r[4]; ri = (lc/r[4]*100) if r[4]>0 else 0
            t_plat = next((p['taxa'] for p in plats if p['nome'] == r[8]), 0.0)
            items_hist.append({'id':r[0],'nome':r[1],'plataforma':r[3],'plataforma_venda':r[8],'custo_raw':r[4],'d_compra_raw':r[5],'venda_raw':r[6],'d_venda_raw':r[7],'anuncio_raw':f"{r[6]/(1-(t_plat/100)):.2f}" if t_plat<100 else r[6],'d_compra':r[5],'d_venda':r[7],'custo':f"{r[4]:.2f}",'venda':f"{r[6]:.2f}",'lucro':f"{lc:.2f}",'roi':f"{ri:.2f}",'color':"text-emerald-400" if lc>=0 else "text-red-400"})
    elif tab not in ['simulador','admin','bets','analytics','import_steam']:
        query = "SELECT id, nome_item, categoria, plataforma, preco_compra, data_compra, in_container, preco_mercado, preco_alvo FROM inventory WHERE status = 'Ativo' AND user_id = ?"
        if tab == 'storage': c.execute(query + " AND in_container = 1 ORDER BY data_compra DESC, id DESC", (uid,))
        elif tab == 'dashboard': c.execute(query + " ORDER BY data_compra DESC, id DESC", (uid,))
        else: c.execute(query + " AND categoria = ? ORDER BY data_compra DESC, id DESC", (uid, active_tab_name))
        for r in c.fetchall():
            try: dt = datetime.datetime.strptime(r[5], "%Y-%m-%d").date()
            except: dt = hoje
            dl = (dt + datetime.timedelta(days=7) - hoje).days
            pm, pa = r[7], r[8]
            la = pm - r[4] if pm > 0 else 0; ra = (la/r[4]*100) if r[4]>0 and pm>0 else 0
            i_data = {'id':r[0],'nome':r[1],'categoria':r[2],'plataforma':r[3],'custo_raw':r[4],'custo':f"{r[4]:.2f}",'data_str':dt.strftime("%d/%m/%Y"),'d_compra_raw':r[5],'lock_text':f"⏱️ {dl}D" if dl>0 else "✅ Livre",'lock_css':"bg-orange-900/50 text-orange-400 border border-orange-800 px-2 py-0.5 rounded text-[10px] font-bold" if dl>0 else "bg-emerald-900/50 text-emerald-400 border border-emerald-800 px-2 py-0.5 rounded text-[10px]", 'mercado_raw':pm,'alvo_raw':pa,'roi_alvo_raw':f"{((pa-r[4])/r[4]*100):.2f}" if r[4]>0 and pa>0 else "0.00",'mercado':f"{pm:.2f}" if pm>0 else "-",'alvo':f"{pa:.2f}" if pa>0 else "-",'lucro_aberto':f"{la:.2f}",'roi_aberto':f"{ra:.2f}",'lucro_css':"text-emerald-400" if la>0 else ("text-red-400" if la<0 else "text-gray-500")}
            if r[6] == 1: items_container.append(i_data)
            elif tab != 'storage': items.append(i_data)
    conn.close()
    return {'active_tab':tab, 'active_tab_name':active_tab_name, 'nav_links':nav_links, 'dropdown_plataformas':plats, 'db_categorias':cats, 'sites_bets':bets, 'carteiras':[{'nome':r['nome'],'valor':r['valor']} for r in saldos_plataformas], 'total_caixa':f"{total_caixa:.2f}", 'passes_arsenal':int(treasury.get('passes_arsenal',0)), 'estrelas':int(treasury.get('estrelas',0)), 'trade_profit':f"{tp:.2f}", 'trade_roi':f"{tr:.2f}", 'trade_profit_color':"text-emerald-400" if tp>=0 else "text-red-400", 'trade_roi_color':"text-emerald-400" if tr>=0 else "text-red-400", 'items':items, 'items_container':items_container, 'items_hist':items_hist, 'count_ativos':count_ativos, 'data_hoje':hoje.strftime("%Y-%m-%d"), 'chart_cat_labels':chart_cat_labels, 'chart_cat_data':chart_cat_data, 'chart_plat_labels':chart_plat_labels, 'chart_plat_data':chart_plat_data, 'chart_trade_labels':chart_trade_labels, 'chart_trade_data':chart_trade_data}

# --- ROUTES ---
@app.route('/')
@login_required
def index(): return render_template('dashboard.html', **get_view_data('dashboard', session['user_id']))

@app.route('/<path:path>')
@login_required
def catch_all(path):
    v = ['storage', 'historico', 'simulador', 'admin', 'bets', 'analytics', 'import_steam']
    if path in v: return render_template(f'{path}.html', **get_view_data(path, session['user_id']))
    if path.startswith('cat_'): return render_template('dashboard.html', **get_view_data(path, session['user_id']))
    return redirect(url_for('index'))

@app.route('/api/fetch_inventory')
@login_required
def fetch_inv():
    s_in = request.args.get('steam_input', '').strip(); sid = s_in
    if 'profiles/' in s_in: sid = s_in.split('profiles/')[1].strip('/').split('/')[0]
    elif 'id/' in s_in:
        v = s_in.split('id/')[1].strip('/').split('/')[0]
        try: sid = re.search(r'<steamID64>(\d+)</steamID64>', requests.get(f"https://steamcommunity.com/id/{v}/?xml=1").text).group(1)
        except: return jsonify({'error': 'Perfil não encontrado.'})
    url = f"https://steamcommunity.com/inventory/{sid}/730/2?l=english&count=2000"
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}); data = r.json()
        if not data.get('success'): return jsonify({'error': 'Privado ou erro.'})
        conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor()
        c.execute("SELECT steam_asset_id FROM inventory WHERE user_id = ?", (session['user_id'],))
        exists = {row[0] for row in c.fetchall()}; conn.close()
        desc = {f"{d['classid']}_{d['instanceid']}": d for d in data.get('descriptions', [])}; inv = []
        for a in data.get('assets', []):
            d_item = desc.get(f"{a['classid']}_{a['instanceid']}", {})
            aid = str(a['assetid'])
            if d_item.get('market_hash_name') and (d_item.get('tradable') != 0 or d_item.get('marketable')): inv.append({'asset_id': aid, 'hash_name': d_item['market_hash_name'], 'icon': f"https://community.akamai.steamstatic.com/economy/image/{d_item.get('icon_url','')}", 'ja_importado': aid in exists})
        return jsonify({'success': True, 'items': inv})
    except: return jsonify({'error': 'Falha.'})

@app.route('/api/save_imported', methods=['POST'])
@login_required
def save_imp():
    its = request.json.get('items', []); conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor()
    for i in its:
        c.execute("SELECT id FROM inventory WHERE steam_asset_id = ? AND user_id = ?", (i['asset_id'], session['user_id']))
        if not c.fetchone(): c.execute("INSERT INTO inventory (nome_item, categoria, plataforma, preco_compra, data_compra, user_id, steam_asset_id) VALUES (?,?,?,?,?,?,?)", (i['nome'], i['categoria'], i['plataforma'], float(i['custo']), i['data'], session['user_id'], i['asset_id']))
    conn.commit(); conn.close(); return jsonify({'success': True})

@app.route('/api/market', methods=['GET'])
def api_market():
    h = request.args.get('hash_name'); url = f"https://steamcommunity.com/market/priceoverview/?appid=730&currency=1&market_hash_name={h}"
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        if r.status_code == 200:
            d = r.json()
            if d.get('success'): return jsonify({'price': float((d.get('lowest_price') or d.get('median_price', '0')).replace('$', '').replace(',', '').strip()), 'success': True})
            return jsonify({'error': 'Não encontrado.'}), 404
        return jsonify({'error': 'Rate Limit' if r.status_code==429 else 'Erro'}), r.status_code
    except: return jsonify({'error': 'Falha.'}), 500

@app.route('/api/start_sync', methods=['POST'])
@login_required
def start_sync():
    global sync_state
    if not sync_state['is_syncing']: threading.Thread(target=background_sync_task, args=(session['user_id'],)).start()
    return jsonify({"success": True})

@app.route('/api/sync_progress', methods=['GET'])
@login_required
def sync_progress():
    global sync_state; return jsonify({'is_syncing': sync_state['is_syncing'], 'total': sync_state['total'], 'current': sync_state['current'], 'item': sync_state['current_item']})

@app.route('/api/bulk_action', methods=['POST'])
@login_required
def bulk_action():
    data = request.json; ids = data.get('ids', []); action = data.get('action'); value = data.get('value')
    if not ids: return jsonify({'success': False})
    conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); ph = ','.join('?' for _ in ids)
    if action == 'target': c.execute(f"UPDATE inventory SET preco_alvo = ? WHERE id IN ({ph}) AND user_id = ?", [float(value)] + ids + [session['user_id']])
    elif action == 'roi':
        for i_id in ids:
            c.execute("SELECT preco_compra FROM inventory WHERE id = ? AND user_id = ?", (i_id, session['user_id'])); custo = c.fetchone()[0]
            c.execute("UPDATE inventory SET preco_alvo = ? WHERE id = ? AND user_id = ?", (custo * (1 + (float(value)/100)), i_id, session['user_id']))
    elif action == 'category': c.execute(f"UPDATE inventory SET categoria = ? WHERE id IN ({ph}) AND user_id = ?", [value] + ids + [session['user_id']])
    conn.commit(); conn.close(); return jsonify({'success': True})

@app.route('/add', methods=['POST'])
@login_required
def add(): conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); c.execute("INSERT INTO inventory (nome_item, categoria, plataforma, preco_compra, data_compra, user_id) VALUES (?,?,?,?,?,?)", (request.form['nome_item'], request.form['categoria'], request.form['plataforma'], float(request.form['preco_compra']), request.form['data_compra'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/add_arsenal', methods=['POST'])
@login_required
def add_arsenal():
    conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); e = int(request.form.get('estrelas_gasto', 0))
    c.execute("SELECT valor FROM tesouraria WHERE chave = 'estrelas' AND user_id = ?", (session['user_id'],)); row = c.fetchone(); sa = float(row[0]) if row else 0.0
    c.execute("UPDATE tesouraria SET valor = ? WHERE chave = 'estrelas' AND user_id = ?", (max(0, sa - e), session['user_id']))
    c.execute("INSERT INTO inventory (nome_item, categoria, plataforma, preco_compra, data_compra, user_id) VALUES (?, ?, 'Arsenal', ?, ?, ?)", (request.form['nome_item'], request.form['categoria'], round(e * 0.4125, 2), request.form['data_compra'], session['user_id']))
    conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/process_session', methods=['POST'])
@login_required
def process_session():
    p = request.form['plataforma']; d_usd = float(request.form['deposito_usd']); ns = request.form.getlist('item_nome[]'); ms = request.form.getlist('item_mercado[]'); cs = request.form.getlist('item_cat[]'); v = []; mt = 0.0
    for i in range(len(ns)):
        if ns[i].strip(): vm = float(ms[i]) if ms[i] else 0.0; mt += vm; v.append({'n': ns[i].strip(), 'm': vm, 'c': cs[i]})
    if mt == 0 or not v: return redirect(request.referrer)
    conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); dh = datetime.date.today().strftime("%Y-%m-%d")
    for i in v: c.execute("INSERT INTO inventory (nome_item, categoria, plataforma, preco_compra, data_compra, preco_mercado, user_id) VALUES (?,?,?,?,?,?,?)", (i['n'], i['c'], p, round(d_usd * (i['m'] / mt), 2), dh, i['m'], session['user_id']))
    conn.commit(); conn.close(); return redirect(url_for('catch_all', path='bets', success=1))
@app.route('/edit_item', methods=['POST'])
@login_required
def edit_item(): conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); c.execute("UPDATE inventory SET nome_item=?, plataforma=?, preco_compra=?, data_compra=?, categoria=? WHERE id=? AND user_id=?", (request.form['nome_item'], request.form['plataforma'], float(request.form['preco_compra']), request.form['data_compra'], request.form['categoria'], request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/delete_item', methods=['POST'])
@login_required
def delete_item(): conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); c.execute("DELETE FROM inventory WHERE id=? AND user_id=?", (request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/sell', methods=['POST'])
@login_required
def sell():
    conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); a, pv = float(request.form['preco_anuncio']), request.form['plataforma_venda']
    c.execute("SELECT taxa FROM plataformas WHERE nome=? AND user_id=?", (pv, session['user_id'])); row = c.fetchone(); tx = float(row[0]) if row else 0.0
    c.execute("UPDATE inventory SET status='Vendido', preco_venda=?, plataforma_venda=?, data_venda=?, in_container=0 WHERE id=? AND user_id=?", (a*(1-(tx/100)), pv, request.form['data_venda'], request.form['item_id'], session['user_id']))
    conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/toggle_container', methods=['POST'])
@login_required
def toggle_container(): conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); c.execute("UPDATE inventory SET in_container=? WHERE id=? AND user_id=?", (int(request.form['status']), request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/edit_history', methods=['POST'])
@login_required
def edit_history():
    conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); a, pv = float(request.form['preco_anuncio']), request.form['plataforma_venda']
    c.execute("SELECT taxa FROM plataformas WHERE nome=? AND user_id=?", (pv, session['user_id'])); row = c.fetchone(); tx = float(row[0]) if row else 0.0
    c.execute("UPDATE inventory SET preco_compra=?, data_compra=?, preco_venda=?, data_venda=?, plataforma_venda=? WHERE id=? AND user_id=?", (float(request.form['preco_compra']), request.form['data_compra'], a*(1-(tx/100)), request.form['data_venda'], pv, request.form['id'], session['user_id']))
    conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/edit_market_prices', methods=['POST'])
@login_required
def edit_market_prices(): conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); c.execute("UPDATE inventory SET preco_mercado=?, preco_alvo=? WHERE id=? AND user_id=?", (float(request.form['preco_mercado'] or 0), float(request.form['preco_alvo'] or 0), request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/update_treasury', methods=['POST'])
@login_required
def update_treasury():
    conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor()
    for k, v in request.form.items():
        if k in ['passes_arsenal', 'estrelas']:
            c.execute("SELECT COUNT(*) FROM tesouraria WHERE chave=? AND user_id=?", (k, session['user_id']))
            if c.fetchone()[0] == 0: c.execute("INSERT INTO tesouraria (chave, valor, user_id) VALUES (?,?,?)", (k, float(v), session['user_id']))
            else: c.execute("UPDATE tesouraria SET valor=? WHERE chave=? AND user_id=?", (float(v), k, session['user_id']))
    conn.commit(); conn.close(); return redirect(request.referrer)

@app.route('/add_platform', methods=['POST'])
@login_required
def add_platform(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("INSERT INTO plataformas (nome, taxa, user_id) VALUES (?,?,?)", (request.form['nome_plataforma'], float(request.form['taxa']), session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/edit_platform', methods=['POST'])
@login_required
def edit_platform(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("UPDATE plataformas SET nome=?, taxa=? WHERE id=? AND user_id=?", (request.form['nome'], float(request.form['taxa']), request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/delete_platform', methods=['POST'])
@login_required
def delete_platform(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("DELETE FROM plataformas WHERE id=? AND user_id=?", (request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/toggle_tesouraria', methods=['POST'])
@login_required
def toggle_tesouraria(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("UPDATE plataformas SET mostrar_tesouraria = CASE WHEN mostrar_tesouraria=1 THEN 0 ELSE 1 END WHERE id=? AND user_id=?", (request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)

@app.route('/add_categoria', methods=['POST'])
@login_required
def add_cat(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("INSERT INTO categorias (nome, user_id) VALUES (?,?)", (request.form['nome_categoria'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/edit_categoria', methods=['POST'])
@login_required
def edit_cat(): conn = sqlite3.connect('inventario_cs2.db'); c = conn.cursor(); c.execute("SELECT nome FROM categorias WHERE id=? AND user_id=?", (request.form['id'], session['user_id'])); vn = c.fetchone()[0]; c.execute("UPDATE categorias SET nome=? WHERE id=? AND user_id=?", (request.form['nome'], request.form['id'], session['user_id'])); c.execute("UPDATE inventory SET categoria=? WHERE categoria=? AND user_id=?", (request.form['nome'], vn, session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/delete_categoria', methods=['POST'])
@login_required
def del_cat(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("DELETE FROM categorias WHERE id=? AND user_id=?", (request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)

@app.route('/add_site_bet', methods=['POST'])
@login_required
def add_bet(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("INSERT INTO sites_bets (nome, user_id) VALUES (?,?)", (request.form['nome'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/edit_site_bet', methods=['POST'])
@login_required
def edit_bet(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("UPDATE sites_bets SET nome=? WHERE id=? AND user_id=?", (request.form['nome'], request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)
@app.route('/delete_site_bet', methods=['POST'])
def del_bet(): conn = sqlite3.connect('inventario_cs2.db'); c=conn.cursor(); c.execute("DELETE FROM sites_bets WHERE id=? AND user_id=?", (request.form['id'], session['user_id'])); conn.commit(); conn.close(); return redirect(request.referrer)

if __name__ == '__main__': app.run(debug=True)