import os
from datetime import datetime
from flask_login import AnonymousUserMixin
from markupsafe import Markup
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, send_file, make_response
)
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, current_user
)
from werkzeug.utils import secure_filename
import uuid
import markdown as md_lib

from config import Config
from models import db, User, Story, Entry

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager = LoginManager(app)

class AnonymousUser(AnonymousUserMixin):
    is_admin = False

login_manager.anonymous_user = AnonymousUser
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---------- 初始化数据库 ----------
with app.app_context():
    db.create_all()

# ---------- 注册 Jinja2 过滤器：渲染 Markdown ----------
@app.template_filter('markdown')
def render_markdown(text):
    return Markup(md_lib.markdown(text, extensions=['extra', 'nl2br']))

# ---------- 辅助：检查文件类型 ----------
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'jpg', 'jpeg', 'png', 'gif'}

# ========== 新增：htmx 局部渲染辅助函数 ==========
def render_page(full_template, content_template, **kwargs):
    """
    若请求来自 htmx (请求头 HX-Request 存在)，只渲染内容片段；
    否则渲染完整页面（含侧边栏）。
    注意：内容片段中应包含 flash 消息的展示。
    """
    if request.headers.get('HX-Request'):
        return render_template(content_template, **kwargs)
    return render_template(full_template, **kwargs)
# =============================================

# ---------- 首页 ----------
@app.route('/')
def index():
    stories = Story.query.order_by(Story.created_at.desc()).all()
    # 修改：使用 render_page，需提供 index_content.html
    return render_page('index.html', 'index_content.html', stories=stories)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return render_page('register.html', 'register_content.html')
        user = User(username=username, password=password)
        db.session.add(user)
        db.session.commit()
        login_user(user)

        # 🔧 关键修改：区分 htmx 和普通请求
        if request.headers.get('HX-Request'):
            resp = make_response('', 200)
            resp.headers['HX-Redirect'] = url_for('index')
            return resp
        else:
            return redirect(url_for('index'))

    return render_page('register.html', 'register_content.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            login_user(user)
            if request.headers.get('HX-Request'):
                resp = make_response('', 200)
                resp.headers['HX-Redirect'] = url_for('index')
                return resp
            else:
                return redirect(url_for('index'))
        flash('用户名或密码错误')
        return render_page('login.html', 'login_content.html')
    return render_page('login.html', 'login_content.html')

# ---------- 登出 ----------
@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# ---------- 创建故事 ----------
@app.route('/story/create', methods=['GET', 'POST'])
@login_required
def create_story():
    if request.method == 'POST':
        # 非管理员才检查每日限制
        if not current_user.is_admin:
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            today_count = Story.query.filter(
                Story.creator_id == current_user.id,
                Story.created_at >= today_start
            ).count()
            if today_count >= 1:
                flash('每个账号每天只能创建一个新故事，请明天再来')
                return redirect(url_for('create_story'))
        # ------------------------------------------
        title = request.form['title'].strip()
        content = request.form['content']
        note = request.form.get('note', '').strip()
        if not title or not content:
            flash('标题和内容不能为空')
            return redirect(url_for('create_story'))
        story = Story(title=title, creator=current_user, status='ongoing')
        db.session.add(story)
        db.session.flush()
        entry = Entry(content=content,
                      note=note if note else None,
                      story_id=story.id,
                      user_id=current_user.id)
        db.session.add(entry)
        db.session.commit()
        return redirect(url_for('story_detail', story_id=story.id))
    # GET 请求使用局部渲染
    return render_page('create_story.html', 'create_story_content.html')

# ---------- 故事详情 + 接龙 ----------
@app.route('/story/<int:story_id>', methods=['GET', 'POST'])
def story_detail(story_id):
    story = Story.query.get_or_404(story_id)
    entries = story.entries
    last_entry = entries[-1] if entries else None

    can_chain = True
    if last_entry and current_user.is_authenticated:
        if last_entry.user_id == current_user.id:
            can_chain = False
    if story.status == 'finished':
        can_chain = False

    if request.method == 'POST' and current_user.is_authenticated and can_chain:
        content = request.form.get('content', '').strip()
        note = request.form.get('note', '').strip()
        if not content:
            flash('接龙内容不能为空')
        else:
            entry = Entry(content=content, note=note if note else None,
                          story_id=story.id, user_id=current_user.id)
            db.session.add(entry)
            db.session.commit()
            return redirect(url_for('story_detail', story_id=story.id))
    # 使用局部渲染
    return render_page('story_detail.html', 'story_detail_content.html',
                       story=story, entries=entries, can_chain=can_chain)

# ---------- 完结故事 ----------
@app.route('/story/<int:story_id>/finish', methods=['POST'])
@login_required
def finish_story(story_id):
    story = Story.query.get_or_404(story_id)
    if story.creator_id == current_user.id:
        story.status = 'finished'
        db.session.commit()
    return redirect(url_for('story_detail', story_id=story_id))

# ---------- 图片上传 ----------
@app.route('/upload_image', methods=['POST'])
@login_required
def upload_image():
    if 'image' not in request.files:
        return {'error': '没有文件'}, 400
    file = request.files['image']
    if file.filename == '':
        return {'error': '空文件名'}, 400
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        url = url_for('static', filename=f'uploads/{filename}')
        return {'url': url}
    return {'error': '不支持的文件类型'}, 400

# ---------- 导出 HTML ----------
@app.route('/story/<int:story_id>/export')
def export_html(story_id):
    story = Story.query.get_or_404(story_id)
    entries = story.entries

    # ---------- 内嵌可爱风格 CSS（保持不变） ----------
    style = '''
    <style>
        :root {
            --pink-100: #fff0f5;
            --pink-200: #ffe4ec;
            --pink-300: #ffb6c1;
            --pink-400: #ff8da1;
            --purple-100: #f3e8ff;
            --shadow-soft: 0 8px 30px rgba(255, 182, 193, 0.25);
            --radius-lg: 24px;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: "Nunito", "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
            background: linear-gradient(135deg, #fce4ec 0%, #f8bbd0 30%, #e1bee7 70%, #f3e5f5 100%);
            background-attachment: fixed;
            min-height: 100vh;
            padding: 40px 20px;
            color: #5c3d4e;
            line-height: 1.8;
            position: relative;
            overflow-x: hidden;
        }

        body::before {
            content: "🌸 ✿ ❀ ✦ 🎀";
            position: fixed;
            top: -10px;
            left: 0;
            width: 100%;
            font-size: 2rem;
            color: rgba(255,255,255,0.35);
            white-space: nowrap;
            pointer-events: none;
            z-index: 0;
            animation: floatText 20s linear infinite;
        }
        @keyframes floatText {
            0% { transform: translateX(-10%); }
            100% { transform: translateX(110%); }
        }

        .container {
            max-width: 800px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
            animation: fadeInUp 0.8s ease;
        }
        @keyframes fadeInUp {
            from { opacity: 0; transform: translateY(30px); }
            to { opacity: 1; transform: translateY(0); }
        }

        h1 {
            text-align: center;
            font-size: 2.5em;
            color: #d47a8c;
            margin-bottom: 0.2em;
            text-shadow: 2px 2px 0 rgba(255,255,255,0.7);
            position: relative;
        }
        h1::before {
            content: "🌸 ";
        }

        .meta {
            text-align: center;
            color: #b87d8b;
            font-size: 1em;
            margin-bottom: 2em;
            background: rgba(255,255,255,0.6);
            display: inline-block;
            padding: 6px 20px;
            border-radius: 30px;
            backdrop-filter: blur(10px);
        }

        .entry {
            background: rgba(255, 255, 255, 0.75);
            backdrop-filter: blur(15px);
            border-radius: var(--radius-lg);
            padding: 1.2em 1.8em;
            margin-bottom: 2em;
            box-shadow: var(--shadow-soft);
            transition: transform 0.3s, box-shadow 0.3s;
        }
        .entry:hover {
            transform: translateY(-3px);
            box-shadow: 0 12px 36px rgba(255, 160, 180, 0.4);
        }

        .entry-header {
            display: flex;
            align-items: baseline;
            margin-bottom: 0.8em;
            border-bottom: 2px dashed #fcc8d0;
            padding-bottom: 0.5em;
            color: #c45b6c;
            font-weight: 700;
        }
        .entry-number {
            font-size: 1.3em;
            margin-right: 8px;
            color: #ff8da1;
        }
        .entry-author {
            margin-right: auto;
        }
        .entry-time {
            font-size: 0.85em;
            color: #b87d8b;
        }

        .entry-note {
            font-size: 0.9em;
            color: #8b6b7a;
            margin-bottom: 1em;
            padding: 6px 12px;
            background: rgba(255, 240, 245, 0.7);
            border-left: 4px solid #ffb6c1;
            border-radius: 8px;
            font-style: italic;
        }

        .entry-content {
            font-size: 1.05em;
            text-align: justify;
        }
        .entry-content img {
            max-width: 100%;
            border-radius: 12px;
            margin: 0.8em 0;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }

        .footer {
            text-align: center;
            margin-top: 3em;
            color: #d8b0b8;
            font-size: 0.9em;
            background: rgba(255,255,255,0.5);
            padding: 10px 20px;
            border-radius: 30px;
            display: inline-block;
            backdrop-filter: blur(10px);
        }

        .fixed-bear {
            position: fixed;
            bottom: 20px;
            right: 20px;
            font-size: 3rem;
            opacity: 0.4;
            pointer-events: none;
            z-index: 2;
        }

        @media print {
            body {
                background: white;
                color: black;
            }
            .entry {
                box-shadow: none;
                border: 1px solid #ddd;
            }
            .fixed-bear, body::before {
                display: none;
            }
        }
    </style>
    '''

    # ---------- 构建 HTML 内容 ----------
    html_parts = [f'<h1>{story.title}</h1>']
    html_parts.append(f'<div style="text-align:center;"><span class="meta">共 {len(entries)} 段 · 创作于 {story.created_at.strftime("%Y-%m-%d")} · {"已完结" if story.status=="finished" else "连载中"}</span></div>')

    for i, entry in enumerate(entries, 1):
        author = entry.author.username
        time_str = entry.created_at.strftime('%Y-%m-%d %H:%M')
        body = md_lib.markdown(entry.content, extensions=['extra', 'nl2br'])
        note_html = ''
        if entry.note:
            note_html = f'<div class="entry-note">💬 {entry.note}</div>'
        html_parts.append(f'''
        <div class="entry">
            <div class="entry-header">
                <span class="entry-number">#{i}</span>
                <span class="entry-author">{author}</span>
                <span class="entry-time">{time_str}</span>
            </div>
            {note_html}
            <div class="entry-content">{body}</div>
        </div>
        ''')

    html_parts.append('<div style="text-align:center;"><div class="footer">✨ 由「小说接龙」生成 · 可离线阅读</div></div>')
    html_parts.append('<div class="fixed-bear">🧸</div>')

    full_html = f'''<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>{story.title}</title>
    {style}
</head>
<body>
    <div class="container">
        {"".join(html_parts)}
    </div>
</body>
</html>'''

    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.html', mode='w', encoding='utf-8')
    tmp.write(full_html)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name=f'{story.title}.html')

# ---------- 删除故事 ----------
@app.route('/story/<int:story_id>/delete', methods=['POST'])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    if story.creator_id != current_user.id and not current_user.is_admin:
        flash('只有故事创建者或管理员才能删除')
        return redirect(url_for('story_detail', story_id=story.id))
    
    Entry.query.filter_by(story_id=story.id).delete()
    db.session.delete(story)
    db.session.commit()
    flash('故事已删除')
    return redirect(url_for('index'))

# ---------- 删除接龙段落 ----------
@app.route('/story/<int:story_id>/entry/<int:entry_id>/delete', methods=['POST'])
@login_required
def delete_entry(story_id, entry_id):
    story = Story.query.get_or_404(story_id)
    if story.creator_id != current_user.id and not current_user.is_admin:
        flash('只有故事创建者或管理员才能删除接龙段落')
        return redirect(url_for('story_detail', story_id=story.id))

    entry = Entry.query.get_or_404(entry_id)
    if entry.story_id != story.id:
        flash('段落不属于此故事')
        return redirect(url_for('story_detail', story_id=story.id))

    db.session.delete(entry)
    db.session.commit()
    flash('段落已删除')
    return redirect(url_for('story_detail', story_id=story.id))

if __name__ == '__main__':
    app.run(debug=True)