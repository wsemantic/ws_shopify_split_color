from odoo import models, _

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
                    price_incl = float(line.get('price'))

                    # Calcular la tasa total de IVA a partir de tax_lines, o definir una tasa fija
                    tax_rate_total = 0.0
                    for tax_line in line.get('tax_lines', []):
                        if tax_line.get('rate'):
                            tax_rate_total += float(tax_line.get('rate'))
                    # En caso de que no exista información de impuestos, se puede asumir 0%
                    if tax_rate_total:
                        price_excl = price_incl / (1 + tax_rate_total)
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

            if order.get('shipping_line'):
                shipping = self.env['delivery.carrier'].sudo().search(
                    [('name', '=', order.get('shipping_line').get('title'))], limit=1)
                if not shipping:
                    delivery_product = self.env['product.product'].sudo().create({
                        'name': order.get('shipping_line').get('title'),
                        'detailed_type': 'product',
                    })
                    vals = {
                        'is_shopify': True,
                        'shopify_instance_id': shopify_instance_id.id,
                        'name': order.get('shipping_line').get('title'),
                        'product_id': delivery_product.id,
                    }
                    shipping = self.env['delivery.carrier'].sudo().create(vals)
                if shipping and shipping.product_id:
                    shipping_vals = {
                        'product_id': shipping.product_id.id,
                        'name': "Shipping",
                        'price_unit': float(order.get('shipping_line').get('price')),
                        'order_id': shopify_order_id.id,
                        'tax_id': [(6, 0, [])]
                    }
                    shipping_so_line = self.env['sale.order.line'].sudo().create(shipping_vals)

        return True
