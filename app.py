import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from markupsafe import Markup
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
import uuid
import markdown as md_lib

from config import Config
from models import db, User, Story, Entry

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)
login_manager = LoginManager(app)
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

# ---------- 首页 ----------
@app.route('/')
def index():
    stories = Story.query.order_by(Story.created_at.desc()).all()
    return render_template('index.html', stories=stories)

# ---------- 注册 ----------
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash('用户名已存在')
            return redirect(url_for('register'))
        user = User(username=username, password=password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('index'))
    return render_template('register.html')

# ---------- 登录 ----------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            login_user(user)
            return redirect(url_for('index'))
        flash('用户名或密码错误')
    return render_template('login.html')

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
        title = request.form['title'].strip()
        content = request.form['content']
        if not title or not content:
            flash('标题和内容不能为空')
            return redirect(url_for('create_story'))
        story = Story(title=title, creator=current_user, status='ongoing')
        db.session.add(story)
        db.session.flush()
        entry = Entry(content=content, story_id=story.id, user_id=current_user.id)
        db.session.add(entry)
        db.session.commit()
        return redirect(url_for('story_detail', story_id=story.id))
    return render_template('create_story.html')

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
        if not content:
            flash('接龙内容不能为空')
        else:
            entry = Entry(content=content, story_id=story.id, user_id=current_user.id)
            db.session.add(entry)
            db.session.commit()
            return redirect(url_for('story_detail', story_id=story.id))
    return render_template('story_detail.html',
                           story=story, entries=entries,
                           can_chain=can_chain)

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
# ---------- 导出 HTML（美化版） ----------
@app.route('/story/<int:story_id>/export')
def export_html(story_id):
    story = Story.query.get_or_404(story_id)
    entries = story.entries

    # ---------- 内嵌 CSS 样式 ----------
    style = '''
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Segoe UI", "Noto Serif SC", "华文楷体", Georgia, serif;
            background: #fdfaf6;
            color: #333;
            max-width: 800px;
            margin: 40px auto;
            padding: 20px;
            line-height: 1.8;
        }
        h1 {
            text-align: center;
            font-size: 2.2em;
            margin-bottom: 0.3em;
            color: #4a2c2a;
            border-bottom: 2px solid #d9b382;
            padding-bottom: 0.3em;
        }
        .meta {
            text-align: center;
            color: #888;
            font-size: 0.9em;
            margin-bottom: 2em;
        }
        .entry {
            margin-bottom: 2em;
            padding: 1em 1.5em;
            background: #fffbf5;
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.05);
            transition: 0.2s;
        }
        .entry:hover {
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .entry-header {
            display: flex;
            align-items: baseline;
            margin-bottom: 0.8em;
            border-bottom: 1px dashed #e0cfba;
            padding-bottom: 0.4em;
        }
        .entry-number {
            font-weight: bold;
            font-size: 1.2em;
            color: #b45f3a;
            margin-right: 0.5em;
        }
        .entry-author {
            font-weight: bold;
            color: #5c3d2e;
        }
        .entry-time {
            margin-left: auto;
            font-size: 0.85em;
            color: #999;
        }
        .entry-content {
            font-size: 1.05em;
            text-align: justify;
        }
        .entry-content img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
            margin: 0.5em 0;
        }
        .footer {
            text-align: center;
            margin-top: 3em;
            color: #bbb;
            font-size: 0.85em;
            border-top: 1px solid #eaeaea;
            padding-top: 1em;
        }
    </style>
    '''

    # ---------- 构建内容 ----------
    html_parts = [f'<h1>{story.title}</h1>']
    html_parts.append(f'<div class="meta">共 {len(entries)} 段 · 创作于 {story.created_at.strftime("%Y-%m-%d")} · 状态：{"已完结" if story.status=="finished" else "连载中"}</div>')

    for i, entry in enumerate(entries, 1):
        author = entry.author.username
        time_str = entry.created_at.strftime('%Y-%m-%d %H:%M')
        body = md_lib.markdown(entry.content, extensions=['extra', 'nl2br'])
        html_parts.append(f'''
        <div class="entry">
            <div class="entry-header">
                <span class="entry-number">#{i}</span>
                <span class="entry-author">{author}</span>
                <span class="entry-time">{time_str}</span>
            </div>
            <div class="entry-content">{body}</div>
        </div>
        ''')

    html_parts.append('<div class="footer">✨ 由「小说接龙」生成 · 可离线阅读</div>')

    full_html = f'''<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <title>{story.title}</title>
    {style}
</head>
<body>
    {"".join(html_parts)}
</body>
</html>'''

    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.html', mode='w', encoding='utf-8')
    tmp.write(full_html)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name=f'{story.title}.html')

@app.template_filter('markdown')
def render_markdown(text):
    import markdown
    # safe_mode 或 extensions 根据需要调整
    return markdown.markdown(text, extensions=['extra', 'nl2br'])

# ---------- 删除故事 ----------
@app.route('/story/<int:story_id>/delete', methods=['POST'])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    # 只允许故事创建者删除
    if story.creator_id != current_user.id:
        flash('只有故事创建者才能删除')
        return redirect(url_for('story_detail', story_id=story.id))
    
    # 删除所有关联的接龙段落
    Entry.query.filter_by(story_id=story.id).delete()
    # 删除故事本身
    db.session.delete(story)
    db.session.commit()
    flash('故事已删除')
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)