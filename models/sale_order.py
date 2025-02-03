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
