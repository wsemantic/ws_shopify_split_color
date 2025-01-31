# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import json
import requests

_logger = logging.getLogger(__name__)

_logger = logging.getLogger(__name__)

class ShopifyInstance(models.Model):
    _inherit = 'shopify.instance'

    last_export_product = fields.Datetime(string="Última exportación de productos")
    last_export_stock = fields.Datetime(string="Última actualización de stock")
    split_products_by_color = fields.Boolean(string="Split Products by Color", default=False)
    color_option_position = fields.Integer(string="Color Option Position", default=1, help="Define en qué opción de Shopify se mapeará el color (por defecto, en la opción 1).")
    size_option_position = fields.Integer(string="Size Option Position", default=2, help="Define en qué opción de Shopify se mapeará la talla (por defecto, en la opción 2).")


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shopify_inventory_item_id = fields.Char(string="Shopify Inventory Item ID")
    
class ProductTemplateSplitColor(models.Model):
    _inherit = 'product.template'

    def _prepare_shopify_variant_data(self, variant, instance_id, color_value, is_color_split=False, is_update=False):
        """Prepara los datos de la variante para enviar a Shopify"""
        variant_data = {
            "price": str(variant.lst_price),
            "sku": variant.default_code or "",
            "barcode": variant.barcode or "",
            "inventory_management": "shopify"
        }

        # Si es una actualización y tenemos el ID de la variante en Shopify, lo incluimos
        if is_update and variant.shopify_variant_id:
            variant_data["id"] = variant.shopify_variant_id

        if is_color_split:
            # Si estamos separando por colores, solo usamos el atributo talla
            
            size_option_key = f"option{instance_id.size_option_position}"
            color_option_key = f"option{instance_id.color_option_position}"
            
            variant_data[color_option_key] = color_value.name if is_color_split and color_value else ""
            size_value = variant.product_template_attribute_value_ids.filtered(lambda v: v.attribute_id.name.lower() != 'color')
            variant_data[size_option_key] = size_value.name if size_value else "Default"
        else:
            # Caso normal - todos los atributos
            for idx, attr_val in enumerate(variant.product_template_attribute_value_ids, 1):
                if idx <= 3:  # Shopify solo permite 3 opciones
                    variant_data[f"option{idx}"] = attr_val.name

        return variant_data

    def export_products_to_shopify(self, shopify_instance_ids, update=False):
        """
        Exporta productos a Shopify, filtrando por aquellos modificados desde la última exportación.
        """
        for instance_id in shopify_instance_ids:
            # Filtrar productos modificados desde la última exportación
            domain = [('write_date', '>', instance_id.last_export_product)] if instance_id.last_export_product else []
            products_to_export = self.search(domain)

            product_count = len(products_to_export)
            _logger.info("WSSH Found %d products to export for instance %s", product_count, instance_id.name)
        
            if not products_to_export:
                _logger.info("WSSH No products to export for instance %s", instance_id.name)
                continue

            headers = {
                "X-Shopify-Access-Token": instance_id.shopify_shared_secret,
                "Content-Type": "application/json"
            }

            # Iterar sobre cada producto a exportar
            for product in products_to_export:
                _logger.info("WSSH Exporting product: %s (ID: %d)", product.name, product.id)
                if not instance_id.split_products_by_color:
                    # Si no hay split por colores, exportar el producto normalmente
                    self._export_single_product(product, instance_id, headers, update)
                    continue

                # Buscar la línea de atributo de color
                color_line = product.attribute_line_ids.filtered(
                    lambda l: l.attribute_id.name.lower() == 'color')

                if not color_line:
                    # Si no hay atributo de color, procesar normalmente
                    self._export_single_product(product, instance_id, headers, update)
                    continue

                # Exportar cada color como un producto separado
                for color_value in color_line.value_ids:
                    # Filtrar variantes para este color
                    variants = product.product_variant_ids.filtered(
                        lambda v: color_value in v.product_template_attribute_value_ids.mapped('product_attribute_value_id')
                    )

                    if not variants:
                        continue

                    # Preparar datos para Shopify
                    variant_data = [
                        self._prepare_shopify_variant_data(variant, instance_id, color_value, True, update)
                        for variant in variants
                    ]

                    product_data = {
                        "product": {
                            "title": f"{product.name} - {color_value.name}",
                            "body_html": product.description or "",
                            "variants": variant_data,
                            "options": [
                                {
                                    "name": "Color",
                                    "position": instance_id.color_option_position,
                                    "values": sorted(set(v.get(f"option{instance_id.color_option_position}", "") for v in variant_data))
                                },
                                {
                                    "name": "Size",
                                    "position": instance_id.size_option_position,
                                    "values": sorted(set(v.get(f"option{instance_id.size_option_position}", "") for v in variant_data))
                                }
                            ],
                            "tags": ','.join(tag.name for tag in product.product_tag_ids)
                        }
                    }

                    # Determinar si crear nuevo o actualizar existente
                    shopify_id = color_line.shopify_product_id
                    if shopify_id and update:
                        url = self.get_products_url(instance_id, f'products/{shopify_id}.json')
                        response = requests.put(url, headers=headers, data=json.dumps(product_data))
                        _logger.info(f"Updating Shopify product {shopify_id}")

                        if response.ok:
                            # Actualizar los IDs de las variantes en Odoo
                            shopify_product = response.json().get('product', {})
                            shopify_variants = shopify_product.get('variants', [])
                            self._update_variant_ids(variants, shopify_variants)
                    else:
                        url = self.get_products_url(instance_id, 'products.json')
                        response = requests.post(url, headers=headers, data=json.dumps(product_data))
                        _logger.info("Creating new Shopify product")

                        if response.ok:
                            shopify_product = response.json().get('product', {})
                            if shopify_product:
                                # Guardar el ID del producto y actualizar los IDs de las variantes
                                color_line.shopify_product_id = shopify_product.get('id')
                                shopify_variants = shopify_product.get('variants', [])
                                self._update_variant_ids(variants, shopify_variants)

                                product.is_shopify_product = True
                                product.shopify_instance_id = instance_id.id
                                product.is_exported = True

                    if not response.ok:
                        _logger.error(f"Error exporting product: {response.text}")
                        raise UserError(f"Error exporting product {product.name} - {color_value.name}: {response.text}")

            # Actualizar la fecha de la última exportación
            instance_id.last_export_product = fields.Datetime.now()

    def _update_variant_ids(self, odoo_variants, shopify_variants):
        """
        Actualiza los IDs de las variantes de Shopify en las variantes de Odoo.
        """
        # Crear un diccionario de variantes de Shopify por SKU
        shopify_variants_by_sku = {
            variant['sku']: {
                'id': variant['id'],
                'inventory_item_id': variant.get('inventory_item_id')
            }
            for variant in shopify_variants 
            if variant.get('sku')
        }

        # Actualizar cada variante de Odoo
        for variant in odoo_variants:
            if variant.default_code in shopify_variants_by_sku:
                variant.shopify_variant_id = shopify_variants_by_sku[variant.default_code]['id']
                variant.shopify_inventory_item_id = shopify_variants_by_sku[variant.default_code]['inventory_item_id']
                _logger.info(f"Updated variant {variant.default_code} with Shopify ID {variant.shopify_variant_id} and inventory item ID {variant.shopify_inventory_item_id}")

    def _export_single_product(self, product, instance_id, headers, update):
        """Exporta un producto sin separación por colores"""
        variant_data = [
            self._prepare_shopify_variant_data(variant, instance_id, False, update)
            for variant in product.product_variant_ids
        ]

        product_data = {
            "product": {
                "title": product.name,
                "body_html": product.description or "",
                "variants": variant_data,
                "tags": ','.join(tag.name for tag in product.product_tag_ids)
            }
        }

        # Añadir opciones si hay atributos
        if product.attribute_line_ids:
            options = []
            for idx, attr_line in enumerate(product.attribute_line_ids, 1):
                if idx <= 3:
                    options.append({
                        "name": attr_line.attribute_id.name,
                        "position": idx,
                        "values": attr_line.value_ids.mapped('name')
                    })
            product_data["product"]["options"] = options

        # Determinar si crear nuevo o actualizar existente
        if product.shopify_product_id and update:
            url = self.get_products_url(instance_id, f'products/{product.shopify_product_id}.json')
            response = requests.put(url, headers=headers, data=json.dumps(product_data))
        else:
            url = self.get_products_url(instance_id, 'products.json')
            response = requests.post(url, headers=headers, data=json.dumps(product_data))

        if response.ok:
            shopify_product = response.json().get('product')
            if shopify_product:
                # Actualizar ID del producto y de sus variantes
                product.shopify_product_id = shopify_product.get('id')
                shopify_variants = shopify_product.get('variants', [])
                self._update_variant_ids(product.product_variant_ids, shopify_variants)

                product.is_shopify_product = True
                product.shopify_instance_id = instance_id.id
                product.is_exported = True
                _logger.info(f"Successfully exported product {product.name}")
        else:
            _logger.error(f"Error exporting product: {response.text}")
            raise UserError(f"Error exporting product {product.name}: {response.text}")

    def get_products_url(self, instance_id, endpoint):
        shop_url = "https://{}.myshopify.com/admin/api/{}/{}".format(instance_id.shopify_host,
                                                                     instance_id.shopify_version, endpoint)
        return shop_url
