from odoo import models, _
from dateutil import parser
from odoo import fields
from datetime import timezone

import logging

_logger = logging.getLogger(__name__)

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def check_customer(self, customer):
        """
        Extiende el método check_customer para asegurar que siempre se tenga un nombre.
        Si el JSON no incluye ni 'first_name' ni 'last_name', se asigna un valor por defecto
        (por ejemplo, usando el email o una cadena fija) antes de llamar al método original.
        """
        _logger.info(f"WSSH Check customer {customer.get('id')}")
        # Si no hay ni first_name ni last_name, definimos un nombre por defecto
        if not (customer.get('first_name') or customer.get('last_name')):            
            # Podemos usar el email o un texto fijo como nombre
            default_name = customer.get('email') or _("Shopify Customer")
            # Creamos una copia modificable del diccionario
            customer = dict(customer)
            customer['first_name'] = default_name
            _logger.info(f"WSSH asignamos name {default_name}")

        # Llamamos al método original (heredado) que se encargará del resto
        return super(SaleOrder, self).check_customer(customer)
        
    
    def create_shopify_order_line(self, shopify_order_id, order, skip_existing_order, shopify_instance_id):
        amount = 0.00
        discount = 0.00
        if order.get('applied_discount'):
            amount = float(order.get('applied_discount').get('amount'))

        if len(order.get('line_items')) > 1:
            discount = amount / len(order.get('line_items'))
        else:
            discount = amount

        dict_tax = {}
        if shopify_order_id.state == 'draft':
            if shopify_order_id.order_line and skip_existing_order == False:
                shopify_order_id.order_line = [(5, 0, 0)]
            for line in order.get('line_items'):
                tax_list = []
                if line.get('tax_lines'):
                    for tax_line in line.get('tax_lines'):
                        dict_tax['name'] = tax_line.get('title')
                        if tax_line.get('rate'):
                            dict_tax['amount'] = tax_line.get('rate') * 100
                        tax = self.env['account.tax'].sudo().search([('name', '=', tax_line.get('title'))], limit=1)
                        if tax:
                            tax.sudo().write(dict_tax)
                        else:
                            tax = self.env['account.tax'].sudo().create(dict_tax)
                        if tax_line.get('price') != '0.00':
                            tax_list.append(tax.id)
                product = self.env['product.product'].search(['|', ('shopify_product_id', '=', line.get('product_id')),
                                                              ('shopify_variant_id', '=', line.get('variant_id'))],
                                                             limit=1)
                if not product:
                    generic_product = self.env.ref('ws_shopify_split_color.product_generic', raise_if_not_found=False)
                    if not generic_product:
                        raise UserError(_("No se ha definido el producto genérico en el sistema."))
                    product = generic_product
                    product_name = "{} - {}".format(generic_product.name, line.get('title'))
                else:
                    product_name = line.get('title')
                    
                if product:
                    # Precio recibido de Shopify (incluye IVA)
                    price_incl = float(line.get('price'))-float(line.get('total_discount'))

                    # Calcular la tasa total de IVA a partir de tax_lines, o definir una tasa fija
                    tax_rate_total = 0.0
                    for tax_line in line.get('tax_lines', []):
                        if tax_line.get('rate'):
                            tax_rate_total += float(tax_line.get('rate'))
                    # En caso de que no exista información de impuestos, se puede asumir 0%
                    if tax_rate_total:
                        price_excl = round(price_incl / (1 + tax_rate_total),2)
                    else:
                        price_excl = price_incl

                    subtotal = price_excl * line.get('quantity')

                    shopify_order_line_vals = {
                        'order_id': shopify_order_id.id,
                        'product_id': product.id,
                        'name': product_name,
                        'product_uom_qty': line.get('quantity'),
                        'price_unit': price_excl,
                        'discount': (discount / subtotal) * 100 if discount else 0.00,
                        'tax_id': [(6, 0, tax_list)]
                    }
                    shopify_order_line_id = self.env['sale.order.line'].sudo().create(shopify_order_line_vals)
            
            for lineship in order.get('shipping_lines'):
                price=round(float(lineship.get('price'))/1.21,2)
                if price>0:
                    shipping = self.env['delivery.carrier'].sudo().search(
                        [('name', '=', lineship.get('title'))], limit=1)
                    if not shipping:
                        delivery_product = self.env['product.product'].sudo().create({
                            'name': lineship.get('title'),
                            'detailed_type': 'product',
                        })
                        vals = {
                            'is_shopify': True,
                            'shopify_instance_id': shopify_instance_id.id,
                            'name': lineship.get('title'),
                            'product_id': delivery_product.id,
                        }
                        shipping = self.env['delivery.carrier'].sudo().create(vals)
                    if shipping and shipping.product_id:
                        shipping_vals = {
                            'product_id': shipping.product_id.id,
                            'name': "Shipping",
                            'price_unit': float(lineship.get('price')),
                            'order_id': shopify_order_id.id,
                            'tax_id': [(6, 0, [])]
                        }
                        shipping_so_line = self.env['sale.order.line'].sudo().create(shipping_vals)

        return True

    def prepare_shopify_order_vals(self, shopify_instance_id, order, skip_existing_order):
        # call a method to check the customer is available or not
        # if not available create a customer
        # if available get the customer id
        # create a sale order
        # create a sale order line
        if order.get('customer'):
            res_partner = self.check_customer(order.get('customer'))
            if res_partner:
                dt = parser.isoparse(order.get('created_at'))
                # Convertir a UTC si es necesario:
                dt_utc = dt.astimezone(timezone.utc)
                date_order_value = fields.Datetime.to_string(dt_utc)
                
                res_partner.shopify_instance_id = shopify_instance_id.id
                shopify_order_id = self.env['sale.order'].sudo().search(
                    [('shopify_order_id', '=', order.get('id'))], limit=1)
                shopify_order_vals = {
                    'partner_id': res_partner.id,
                    'name': order.get('name'),
                    'shopify_instance_id': shopify_instance_id.id,
                    'shopify_order_id': order.get('id'),
                    'shopify_order_number': order.get('order_number'),
                    'shopify_order_status': order.get('status'),
                    'create_date': date_order_value,
                    'date_order': date_order_value,
                    'shopify_order_total': order.get('total_price'),
                    'is_shopify_order': True,
                    'order_shopify_id': order.get('order_id'),
                }
                if not shopify_order_id:
                    shopify_order_id = self.sudo().create(shopify_order_vals)
                    shopify_order_id.state = 'draft'
                else:
                    if shopify_order_id and shopify_order_id.state == 'draft' and skip_existing_order == False:
                        shopify_order_id.sudo().write(shopify_order_vals)
                self.create_shopify_order_line(shopify_order_id, order, skip_existing_order, shopify_instance_id)

                return shopify_order_id
        
    def import_shopify_orders(self, shopify_instance_ids, skip_existing_order, from_date, to_date):
        if shopify_instance_ids == False:
            shopify_instance_ids = self.env['shopify.instance'].sudo().search([('shopify_active', '=', True)])
        for shopify_instance_id in shopify_instance_ids:
            self.import_shopify_draft_orders(shopify_instance_id, skip_existing_order, from_date, to_date)
            # import shopify oders from shopify to odoo
            # call method to connect to shopify

            all_orders = []
            url = self.get_order_url(shopify_instance_id, endpoint='orders.json')
            access_token = shopify_instance_id.shopify_shared_secret
            headers = {
                "X-Shopify-Access-Token": access_token
            }
            if from_date and to_date:
                params = {
                    "limit": 250,  # Adjust the page size as needed
                    "page_info": None,
                    "created_at_min": from_date,
                    "created_at_max": to_date,
                }
            else:
                params = {
                    "limit": 250,  # Adjust the page size as needed
                    "page_info": None
                }
            while True:
                response = requests.get(url, headers=headers, params=params)
                if response.status_code == 200 and response.content:
                    data = response.json()
                    orders = data.get('orders', [])
                    all_orders.extend(orders)

                    page_info = data.get('page_info', {})
                    if 'has_next_page' in page_info and page_info['has_next_page']:
                        params['page_info'] = page_info['next_page']
                    else:
                        break
                else:
                    _logger.info("Error:", response.status_code)
                    break
            if all_orders:
                orders = self.create_shopify_order(all_orders, shopify_instance_id, skip_existing_order, status='open')
                return orders
            else:
                _logger.info("No orders found in shopify")
                return []        