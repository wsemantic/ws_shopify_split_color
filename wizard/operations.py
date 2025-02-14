from odoo import models, api, fields
import logging

_logger = logging.getLogger(__name__)

class ShopifyOperation(models.TransientModel):
    _inherit = 'shopify.operation'
    
    export_shopify_operation = fields.Selection(
        selection_add=[('export_shopify_stock', 'Export Stock')]
    )
    
    def perform_export_shopify_operation(self):
        if self.export_shopify_operation == 'export_shopify_stock':
            updated_products = self.env['product.template'].export_stock_to_shopify(self.shopify_instance_id)
            if updated_products:
                action = self.env.ref("pragtech_odoo_shopify_connector.action_product_product_shopify").sudo().read()[0]
                action["domain"] = [("id", "in", updated_products)]
                return action
            else:
                return {
                    "type": "ir.actions.client",
                    "tag": "reload",
                }
        # Para las demás opciones, llamamos a la implementación original (si existe)
        return super(ShopifyOperation, self).perform_export_shopify_operation()
