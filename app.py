from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, make_response, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
from barcode import EAN13
from barcode.writer import ImageWriter
import random
import string
import os
import csv
import io
import re
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql://root:abcABC&123@localhost/project'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.String(10), unique=True, nullable=False)
    barcode = db.Column(db.String(13), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    initial_quantity = db.Column(db.Integer, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def add_admin_user(username, password):
    new_admin = User(
        username=username,
        password=password,  # Store direct password for MySQL compatibility
        is_admin=True
    )
    try:
        db.session.add(new_admin)
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Error adding user: {e}")
        return False

def generate_unique_barcode():
    while True:
        barcode_number = ''.join(random.choices(string.digits, k=12))
        total = sum(int(barcode_number[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
        check_digit = (10 - (total % 10)) % 10
        barcode = barcode_number + str(check_digit)
        if not Item.query.filter_by(barcode=barcode).first():
            return barcode

def generate_unique_product_id():
    while True:
        product_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not Item.query.filter_by(product_id=product_id).first():
            return product_id

@app.route('/', methods=['GET', 'POST'])
def index():
    items = db.session.query(Item, (Item.price * Item.quantity).label('total_price')).all()
    message = None
    if request.method == 'POST':
        scanned_code = request.form['scanned_code']
        item = Item.query.filter((Item.product_id == scanned_code) | (Item.barcode == scanned_code)).first()
        if item:
            return redirect(url_for('purchase', item_id=item.id))
        else:
            message = "Item not found. Please scan next item."
    return render_template('index.html', items=items, message=message)

@app.route('/purchase/<int:item_id>', methods=['GET', 'POST'])
def purchase(item_id):
    item = Item.query.get_or_404(item_id)
    if request.method == 'POST':
        quantity = int(request.form['quantity'])
        if quantity <= 0:
            flash('Please enter a valid quantity.', 'error')
        elif quantity > item.quantity:
            flash(f'Sorry, only {item.quantity} items are available.', 'error')
        else:
            session['purchase'] = {
                'item_id': item.id,
                'quantity': quantity,
                'total_price': item.price * quantity
            }
            return redirect(url_for('payment'))
    return render_template('purchase.html', item=item)

@app.route('/payment', methods=['GET', 'POST'])
def payment():
    if 'purchase' not in session:
        flash('No purchase to process.', 'error')
        return redirect(url_for('index'))
    
    purchase = session['purchase']
    item = Item.query.get_or_404(purchase['item_id'])
    
    if request.method == 'POST':
        payment_successful = True  # Mock payment process
        
        if payment_successful:
            old_quantity = item.quantity
            item.quantity -= purchase['quantity']
            db.session.commit()
            
            print(f"Item {item.name} updated. Old quantity: {old_quantity}, New quantity: {item.quantity}, Initial quantity: {item.initial_quantity}")
            
            session.pop('purchase', None)
            
            flash(f'Payment successful! You have purchased {purchase["quantity"]} of {item.name}.', 'success')
            return redirect(url_for('index'))
        else:
            flash('Payment failed. Please try again.', 'error')
    
    return render_template('payment.html', item=item, purchase=purchase)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Get user from database
        user = User.query.filter_by(username=username).first()
        
        # Check if user exists and password matches
        if user and user.password == password:
            login_user(user)
            flash('Logged in successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    filter_type = request.args.get('filter', 'all')
    sort_by = request.args.get('sort', 'name')
    
    query = Item.query
    
    if filter_type == 'sold':
        query = query.filter(Item.quantity < Item.initial_quantity)
    elif filter_type == 'not_sold':
        query = query.filter(Item.quantity == Item.initial_quantity)
    
    if sort_by == 'price_high_to_low':
        query = query.order_by(Item.price.desc())
    elif sort_by == 'price_low_to_high':
        query = query.order_by(Item.price.asc())
    else:
        query = query.order_by(Item.name)
    
    items = query.all()
    
    items_with_info = [
        {
            'item': item,
            'sold_quantity': item.initial_quantity - item.quantity,
            'refundable_quantity': item.initial_quantity - item.quantity
        }
        for item in items
    ]
    
    return render_template('admin_dashboard.html', 
                         items=items_with_info, 
                         filter_type=filter_type, 
                         sort_by=sort_by)

@app.route('/refund_page/<int:item_id>')
@login_required
def refund_page(item_id):
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    item = Item.query.get_or_404(item_id)
    max_refund_quantity = item.initial_quantity - item.quantity
    return render_template('refund.html', item=item, max_refund_quantity=max_refund_quantity)

@app.route('/refund/<int:item_id>', methods=['POST'])
@login_required
def refund_item(item_id):
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    item = Item.query.get_or_404(item_id)
    refund_quantity = int(request.form.get('quantity', 0))
    
    if refund_quantity <= 0:
        return jsonify({'success': False, 'message': 'Invalid refund quantity'}), 400
    
    if item.initial_quantity - item.quantity < refund_quantity:
        return jsonify({'success': False, 'message': 'Refund quantity exceeds sold items'}), 400
    
    item.quantity += refund_quantity
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Refunded {refund_quantity} of {item.name}',
        'new_quantity': item.quantity
    })

@app.route('/add_item', methods=['GET', 'POST'])
@login_required
def add_item():
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        barcode = request.form.get('barcode')
        name = request.form.get('name')
        price = request.form.get('price')
        quantity = request.form.get('quantity')
        
        if not re.match(r'^\d{13}$', barcode):
            flash('Barcode must be a 13-digit number.', 'error')
            return render_template('add_item.html')
        
        if not name or not price or not quantity:
            flash('All fields are required.', 'error')
            return render_template('add_item.html')
        
        try:
            price = float(price)
            quantity = int(quantity)
        except ValueError:
            flash('Invalid price or quantity. Please enter numeric values.', 'error')
            return render_template('add_item.html')
        
        existing_item = Item.query.filter_by(barcode=barcode).first()
        if existing_item:
            flash('An item with this barcode already exists.', 'error')
            return render_template('add_item.html')
        
        new_item = Item(
            product_id=generate_unique_product_id(),
            barcode=barcode,
            name=name,
            price=price,
            quantity=quantity,
            initial_quantity=quantity
        )
        db.session.add(new_item)
        
        try:
            db.session.commit()
            flash('Item added successfully.', 'success')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred while adding the item: {str(e)}', 'error')
            return render_template('add_item.html')
    
    return render_template('add_item.html')

@app.route('/edit_item/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_item(id):
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    item = Item.query.get_or_404(id)
    
    if request.method == 'POST':
        barcode = request.form.get('barcode')
        name = request.form.get('name')
        price = request.form.get('price')
        quantity = request.form.get('quantity')
        
        if not barcode or not name or not price or not quantity:
            flash('All fields are required.', 'error')
            return render_template('edit_item.html', item=item)
        
        try:
            price = float(price)
            quantity = int(quantity)
        except ValueError:
            flash('Invalid price or quantity. Please enter numeric values.', 'error')
            return render_template('edit_item.html', item=item)
        
        existing_item = Item.query.filter(Item.barcode == barcode, Item.id != id).first()
        if existing_item:
            flash('An item with this barcode already exists.', 'error')
            return render_template('edit_item.html', item=item)
        
        item.barcode = barcode
        item.name = name
        item.price = price
        item.quantity = quantity
        if quantity > item.initial_quantity:
            item.initial_quantity = quantity
        
        db.session.commit()
        flash('Item updated successfully.', 'success')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('edit_item.html', item=item)

@app.route('/delete_item/<int:id>')
@login_required
def delete_item(id):
    if not current_user.is_admin:
        flash('Access denied.', 'error')
        return redirect(url_for('index'))
    
    item = Item.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Item deleted successfully.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/check_barcode')
def check_barcode():
    barcode = request.args.get('barcode')
    item = Item.query.filter_by(barcode=barcode).first()
    if item:
        return jsonify({
            'exists': True,
            'item': {
                'id': item.id,
                'name': item.name,
                'price': item.price,
                'quantity': item.quantity
            }
        })
    return jsonify({'exists': False})

@app.route('/generate_report')
@login_required
def generate_report():
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    report_type = request.args.get('type', 'pdf')
    items = Item.query.all()
    
    if report_type == 'csv':
        return generate_csv_report(items)
    else:
        return generate_pdf_report(items)

def generate_csv_report(items):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Product ID', 'Barcode', 'Name', 'Price', 'Quantity', 'Initial Quantity', 'Sold Quantity', 'Total'])
    
    total_price = total_quantity = total_initial_quantity = total_sold_quantity = total_total = 0
    
    for item in items:
        sold_quantity = item.initial_quantity - item.quantity
        row_total = item.price * sold_quantity
        writer.writerow([
            item.product_id, 
            item.barcode, 
            item.name, 
            f"${item.price:.2f}", 
            item.quantity, 
            item.initial_quantity,
            sold_quantity,
            f"${row_total:.2f}"
        ])
        
        total_price += item.price
        total_quantity += item.quantity
        total_initial_quantity += item.initial_quantity
        total_sold_quantity += sold_quantity
        total_total += row_total
    
    # Write totals row
    writer.writerow([
        'TOTAL', '', '', 
        f"${total_price:.2f}", 
        total_quantity, 
        total_initial_quantity, 
        total_sold_quantity,
        f"${total_total:.2f}"
    ])
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='inventory_report.csv'
    )

def generate_pdf_report(items):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []

    data = [['Product ID', 'Barcode', 'Name', 'Price', 'Quantity', 'Initial Quantity', 'Sold Quantity', 'Total']]
    
    total_price = total_quantity = total_initial_quantity = total_sold_quantity = total_total = 0
    
    for item in items:
        sold_quantity = item.initial_quantity - item.quantity
        row_total = item.price * sold_quantity
        data.append([
            item.product_id, 
            item.barcode, 
            item.name, 
            f"${item.price:.2f}", 
            item.quantity, 
            item.initial_quantity,
            sold_quantity,
            f"${row_total:.2f}"
        ])
        
        total_price += item.price
        total_quantity += item.quantity
        total_initial_quantity += item.initial_quantity
        total_sold_quantity += sold_quantity
        total_total += row_total
    
    # Add totals row
    data.append([
        'TOTAL', '', '', 
        f"${total_price:.2f}", 
        total_quantity, 
        total_initial_quantity, 
        total_sold_quantity,
        f"${total_total:.2f}"
    ])

    table = Table(data)
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 12),
        ('TOPPADDING', (0, 1), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BACKGROUND', (-1, 0), (-1, -1), colors.lightblue),  # Highlight total column
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgreen),  # Highlight total row
    ])
    table.setStyle(style)
    elements.append(table)
    
    doc.build(elements)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name='inventory_report.pdf'
    )

# Route to add admin users (for development)
@app.route('/add_admin/<username>/<password>')
def add_admin(username, password):
    if add_admin_user(username, password):
        return f"Admin user {username} created successfully"
    return "Error creating admin user"

@app.cli.command("reset_initial_quantities")
def reset_initial_quantities():
    with app.app_context():
        items = Item.query.all()
        for item in items:
            if item.initial_quantity is None or item.initial_quantity < item.quantity:
                item.initial_quantity = item.quantity
        db.session.commit()
    print("Initial quantities have been reset.")

@app.cli.command("reset_db")
def reset_db():
    with app.app_context():
        db.drop_all()
        db.create_all()
        # Create a default admin user
        add_admin_user('admin', '1234')
    print("Database has been reset and admin user created.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
