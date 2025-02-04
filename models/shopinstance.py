import json

import requests,re
from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)

class ShopifyInstance(models.Model):
    _inherit = 'shopify.instance'

    last_export_customer = fields.Datetime(string="Última exportación de clientes")
    last_export_product = fields.Datetime(string="Última exportación de productos")
    last_export_stock = fields.Datetime(string="Última actualización de stock")
    split_products_by_color = fields.Boolean(string="Split Products by Color", default=False)
    color_option_position = fields.Integer(string="Color Option Position", default=1, help="Define en qué opción de Shopify se mapeará el color (por defecto, en la opción 1).")
    size_option_position = fields.Integer(string="Size Option Position", default=2, help="Define en qué opción de Shopify se mapeará la talla (por defecto, en la opción 2).")
    
    def _parse_link_header(self,link_header):
        # Busca patrones del tipo:
        # <URL>; rel="next", <URL>; rel="previous", etc.
        pattern = r'<([^>]+)>;\s*rel="(\w+)"'
        matches = re.findall(pattern, link_header)
        # matches será lista de tuplas [(url, rel), (url, rel), ...]
        links = {}
        for url, rel in matches:
            links[rel] = url
        return links    
        
    def clean_string(self,text):
        """
        Elimina los backslashes que generan secuencias de escape no deseadas,
        excepto aquellas que formen parte de secuencias válidas (por ejemplo, \n, \t, etc.).
        En este ejemplo, se reemplaza cualquier '\' que no esté seguido por 'n', 't', 'r' o '\' por una cadena vacía.
        """
        # Esta expresión regular busca un '\' que no vaya seguido de n, t, r o \
        cleaned = re.sub(r'\\(?![ntr\\])', '', text)
        return cleaned
