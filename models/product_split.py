# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging
import json
import requests
import re

_logger = logging.getLogger(__name__)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    shopify_inventory_item_id = fields.Char(string="Shopify Inventory Item ID")
    
class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    shopify_product_id = fields.Char(string="Shopify Product ID")
    
class ProductTemplateSplitColor(models.Model):
    _inherit = 'product.template'

    def _prepare_shopify_variant_data(self, variant, instance_id, template_attribute_value=None, is_color_split=False, is_update=False):
        """Prepara los datos de la variante para enviar a Shopify"""
        variant_data = {
            "price": variant.lst_price,
            "sku": variant.default_code or "",
            "barcode": variant.barcode or "",
            "inventory_management": "shopify"
        }

        # Si es una actualización y tenemos el ID de la variante en Shopify, lo incluimos
        if is_update and variant.shopify_variant_id:
            variant_data["id"] = variant.shopify_variant_id

        if is_color_split and template_attribute_value:
            # Si estamos separando por colores, solo usamos el atributo talla
            size_option_key = f"option{instance_id.size_option_position}"
            color_option_key = f"option{instance_id.color_option_position}"
            
            variant_data[color_option_key] = template_attribute_value.name if is_color_split and template_attribute_value else ""
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
        color_attribute = None
        for attr in self.env['product.attribute'].search([]):
            # Supongamos que quieres el valor en inglés, 'en_US'
            if attr.name and attr.name.lower().find('color') != -1:
                color_attribute = attr
                break

        for instance_id in shopify_instance_ids:                                                                             
            # Filtrar productos modificados desde la última exportación
            if instance_id.last_export_product:
                _logger.info(f"WSSH Starting product export por fecha {instance_id.last_export_product} instance {instance_id.name} atcolor {color_attribute}") 
                domain = [
                    ('is_published', '=', True),
                    ('write_date', '>', instance_id.last_export_product)
                ]
            else:
                _logger.info("WSSH Starting product export SIN fecha for instance %s", instance_id.name)
                domain = [
                        ('is_published', '=', True),
                        ('is_shopify_product', '=', False)
                ]

            products_to_export = self.search(domain, order='is_shopify_product,create_date')

            product_count = len(products_to_export)
            _logger.info("WSSH Found %d products to export for instance %s", product_count, instance_id.name)
        
            if not products_to_export:
                _logger.info("WSSH No products to export for instance %s", instance_id.name)
                continue

            headers = {
                "X-Shopify-Access-Token": instance_id.shopify_shared_secret,
                "Content-Type": "application/json"
            }

            processed_count = 0
            max_processed = 10  # Limitar a 10 productos exportados por ejecución
        
            # Iterar sobre cada producto a exportar
            for product in products_to_export:                
                #if 2>1:
                #    continue
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
                for template_attribute_value in color_line.product_template_value_ids:
                    _logger.info(f"WSSH Exporting product: {product.name} (ID:{product.id}) update {update} variante {template_attribute_value.name}")
                    response = None
                    # Filtrar variantes para este color
                    variants = product.product_variant_ids.filtered(
                        lambda v: template_attribute_value in v.product_template_attribute_value_ids
                    )

                    if not variants:
                        _logger.info(f"WSSH No hay variantes con codigo {template_attribute_value.name}")
                        continue

                    # Preparar datos para Shopify
                    variant_data = [
                        self._prepare_shopify_variant_data(variant, instance_id, template_attribute_value, True, update)
                        for variant in variants
                        if variant.default_code
                    ]
                    
                    # Si no hay variantes con default_code, se salta este producto virtual
                    if not variant_data:
                        _logger.info("WSSH Skipping Shopify export for product '%s' with color '%s' because no variant has default_code",
                                     product.name, template_attribute_value.name)
                        continue

                    product_data = {
                        "product": {
                            "title": f"{product.name} - {template_attribute_value.name}",
                            "body_html": product.description or "",
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

                    # Si el producto ya existe, solo actualizamos el producto y sus opciones
                    if template_attribute_value.shopify_product_id:  # Acceso correcto al campo
                        if update:
                            product_data["product"]["id"] = template_attribute_value.shopify_product_id
                            url = self.get_products_url(instance_id, f'products/{template_attribute_value.shopify_product_id}.json')
                            response = requests.put(url, headers=headers, data=json.dumps(product_data))
                            _logger.info(f"WSSH Updating Shopify product {template_attribute_value.shopify_product_id}")

                            if response.ok:
                                # Actualizar las variantes individualmente
                                processed_count += 1
                                for variant in variants:
                                    self._update_shopify_variant(variant, instance_id, headers)
                        else:
                            _logger.info(f"WSSH Existe variant id pero no Update {template_attribute_value.shopify_product_id}")
                    else:
                        # Si es un nuevo producto, enviamos también las variantes
                        product_data["product"]["variants"] = variant_data
                        product_data["product"]["status"]='draft'
                        url = self.get_products_url(instance_id, 'products.json')
                        response = requests.post(url, headers=headers, data=json.dumps(product_data))
                        _logger.info("WSSHCreating new Shopify product")

                        if response.ok:
                            processed_count += 1
                            shopify_product = response.json().get('product', {})
                            if shopify_product:
                                # Guardar el ID del producto y actualizar los IDs de las variantes
                                template_attribute_value.shopify_product_id = shopify_product.get('id')  # Asignación correcta del campo
                                shopify_variants = shopify_product.get('variants', [])
                                self._update_variant_ids(variants, shopify_variants)

                                product.is_shopify_product = True
                                product.shopify_instance_id = instance_id.id
                                product.is_exported = True

                    if response is not None and not response.ok:
                        _logger.error(f"WSSH Error exporting product: {response.text}")
                        raise UserError(f"WSSH Error exporting product {product.name} - {template_attribute_value.name}: {response.text}")

            if processed_count >= max_processed:
                _logger.info("WSSH Processed %d products for instance %s. Stopping export for this run.", processed_count, instance_id.name)
                break
                
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
                variant.is_shopify_variant=True
                variant.shopify_barcode=variant.default_code
                _logger.info(f"WSSH Updated variant {variant.default_code} with Shopify ID {variant.shopify_variant_id} and inventory item ID {variant.shopify_inventory_item_id}")

    def _export_single_product(self, product, instance_id, headers, update):
        """Exporta un producto sin separación por colores"""
        variant_data = [
            self._prepare_shopify_variant_data(variant, instance_id, is_update=update)
            for variant in product.product_variant_ids
            if variant.default_code
        ]

        product_data = {
            "product": {
                "title": product.name,
                "body_html": product.description or "",
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

        # Si el producto ya existe, solo actualizamos el producto y sus opciones
        if product.shopify_product_id and update:
            product_data["product"]["id"] = product.shopify_product_id
            url = self.get_products_url(instance_id, f'products/{product.shopify_product_id}.json')
            response = requests.put(url, headers=headers, data=json.dumps(product_data))
            
            if response.ok:
                # Actualizar las variantes individualmente
                for variant in product.product_variant_ids:
                    if variant.default_code:
                        self._update_shopify_variant(variant, instance_id, headers)
                    
        else:
            # Si es un nuevo producto, enviamos también las variantes
            product_data["product"]["status"]='draft'
            product_data["product"]["variants"] = variant_data
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
                _logger.info(f"WSSH Successfully exported product {product.name}")
        else:
            _logger.error(f"WSSH Error exporting product: {response.text}")
            raise UserError(f"WSSH Error exporting product {product.name}: {response.text}")

    def get_products_url(self, instance_id, endpoint):
        shop_url = "https://{}.myshopify.com/admin/api/{}/{}".format(instance_id.shopify_host,
                                                                     instance_id.shopify_version, endpoint)
        return shop_url
        
    def _update_shopify_variant(self, variant, instance_id, headers):
        """Actualiza una variante en Shopify usando el endpoint variants/<id_variant>.json"""
        variant_data = self._prepare_shopify_variant_data(variant, instance_id, is_update=True)
        url = self.get_products_url(instance_id, f'variants/{variant.shopify_variant_id}.json')
        response = requests.put(url, headers=headers, data=json.dumps({"variant": variant_data}))
        
        if response.ok:
            _logger.info(f"WSSH Successfully updated variant {variant.default_code} in Shopify")
        else:
            _logger.error(f"WSSH Error updating variant {variant.default_code}: {response.text}")
            raise UserError(f"WSSH Error updating variant {variant.default_code}: {response.text}")
            
    def import_shopify_products(self, shopify_instance_ids, skip_existing_products, from_date, to_date):
        if not shopify_instance_ids:
            shopify_instance_ids = self.env['shopify.instance'].sudo().search([('shopify_active', '=', True)])
        
        for shopify_instance_id in shopify_instance_ids:
            _logger.info("WSSH Starting product import for instance %s", shopify_instance_id.name)                                                                                                  
            url = self.get_products_url(shopify_instance_id, endpoint='products.json')
            access_token = shopify_instance_id.shopify_shared_secret
            headers = {
                "X-Shopify-Access-Token": access_token,
            }
            
            # Parámetros para la solicitud
            params = {
                "limit": 250,  # Ajustar el tamaño de la página según sea necesario
                "order": "id asc",
                "page_info": None,
            }
            
            if from_date and to_date:
                params.update({
                    "created_at_min": from_date,
                    "created_at_max": to_date,
                })
            
            all_products = []
            while True:
                #_logger.info("WSSH Shopify POST response JSON: %s", json.dumps(response.json(), indent=4))
                response = requests.get(url, headers=headers, params=params)                
                if response.status_code == 200 and response.content:
                    shopify_products = response.json()
                    products = shopify_products.get('products', [])
                    all_products.extend(products)
                    _logger.info("WSSH All products fetched : %d", len(all_products))
                    # Verificar si hay más páginas                        
                    link_header = response.headers.get('Link')
                    if link_header:
                        links = shopify_instance_id._parse_link_header(link_header)
                        if 'next' in links:
                            url = links['next']
                            params = None                            
                            continue
                break              
            _logger.info("WSSH Total products fetched from Shopify: %d", len(all_products))
             
            if all_products:
                # Procesar los productos importados
                products = self._process_imported_products(all_products, shopify_instance_id, skip_existing_products)
                return products
            else:
                _logger.info("WSSHProducts not found in Shopify store")
                return []

    def _process_imported_products(self, shopify_products, shopify_instance_id, skip_existing_products):
      product_list = []
      for shopify_product in shopify_products:
          _logger.info("WSSH Processing Shopify product ID: %s", shopify_product.get('id'))
          shopify_product_id = shopify_product.get('id')
          
          # Buscar si el producto ya existe en Odoo por shopify_product_id en product.template.attribute.value
          existing_attribute_value = self.env['product.template.attribute.value'].sudo().search([
              ('shopify_product_id', '=', shopify_product_id),
          ], limit=1)
          
          if existing_attribute_value:
              # Si el producto ya existe, no hacer nada
              _logger.info(f"WSSH Product with Shopify ID {shopify_product_id} already exists in Odoo.")
              product_list.append(existing_attribute_value.product_tmpl_id.id)
              continue
          
          # Si no existe, buscar por las variantes (shopify_variant_id o default_code)
          for variant in shopify_product.get('variants', []):
              shopify_variant_id = variant.get('id')
              sku = variant.get('sku')
              _logger.info(f"WSSH iterando varian {sku}")
              # Buscar por shopify_variant_id o default_code (SKU)
              existing_variant = self.env['product.product'].sudo().search([
                  '|',
                  ('shopify_variant_id', '=', shopify_variant_id),
                  ('default_code', '=', sku),  # OR para default_code
              ], limit=1)
              
              if existing_variant:
                  # Filtramos los valores de atributo cuyo atributo sea "color"
                  color_values = existing_variant.product_template_attribute_value_ids.filtered(
                      lambda v: v.attribute_id.name.lower() == 'color'
                  )
                  if color_values:
                      color_values.write({'shopify_product_id': shopify_product_id})
                      for template_value in color_values:
                          _logger.info(f"WSSH Updated color attribute value {template_value.name} with Shopify ID {shopify_product_id}.")                              
                  
                  self._update_variant_ids([existing_variant], [variant])                  
                  
                  # Marcar el producto como exportado
                  existing_variant.product_tmpl_id.write({
                      'is_shopify_product': True,
                      'shopify_instance_id': shopify_instance_id.id,
                      'is_exported': True,
                  })
                  
                  _logger.info(f"WSSH Updated existing product template {existing_variant.product_tmpl_id.name} with Shopify ID {shopify_product_id}.")
                  product_list.append(existing_variant.product_tmpl_id.id)
              else:
                  _logger.info("WSSH No matching product found for Shopify Variant ID: %s or SKU: %s", shopify_variant_id, sku)
          else:
              # Si no se encuentra el producto ni sus variantes, crear el producto en Odoo
              if not skip_existing_products:
                  _logger.info(f"WSSH Creando producto ")
                  #product_template = self._create_product_from_shopify(shopify_product, shopify_instance_id)
                  #if product_template:
                  #    product_list.append(product_template.id)
      
      return product_list

    def _create_product_from_shopify(self, shopify_product, shopify_instance_id):
        """Crea un producto en Odoo a partir de un producto de Shopify."""
        tags = shopify_product.get('tags')
        tag_list = []
        if tags:
            tags = tags.split(',')
            for tag in tags:
                tag_id = self.env['product.tag'].sudo().search([('name', '=', tag)], limit=1)
                if not tag_id:
                    tag_id = self.env['product.tag'].sudo().create({'name': tag})
                    tag_list.append(tag_id.id)
                else:
                    tag_list.append(tag_id.id)
        
        description = False
        if shopify_product.get('body_html'):
            soup = BeautifulSoup(shopify_product.get('body_html'), 'html.parser')
            description_converted_to_text = soup.get_text()
            description = description_converted_to_text
        
        product_vals = {
            'name': shopify_product.get('title'),
            'is_shopify_product': True,
            "detailed_type": "product",
            'shopify_instance_id': shopify_instance_id.id,
            'default_code': shopify_product.get('sku') if shopify_product.get('sku') else '',
            'barcode': shopify_product.get('barcode') if shopify_product.get('barcode') else '',
            'shopify_barcode': shopify_product.get('barcode') if shopify_product.get('barcode') else '',
            'shopify_sku': shopify_product.get('sku') if shopify_product.get('sku') else '',
            'description_sale': description if description else False,
            'description': shopify_product.get('body_html') if shopify_product.get('body_html') else False,
            'taxes_id': [(6, 0, [])],
            'product_tag_ids': [(6, 0, tag_list)],
        }
        
        # Crear el producto en Odoo
        product_template = self.env['product.template'].sudo().create(product_vals)
        
        # Asignar el shopify_product_id a las líneas de atributos
        for attribute_line in product_template.attribute_line_ids:
            for attribute_value in attribute_line.product_template_value_ids:
                if attribute_value.attribute_id.name.lower() == 'color':
                    attribute_value.write({
                        'shopify_product_id': shopify_product.get('id'),
                    })
        
        _logger.info(f"WSSH Created new product template {product_template.name} from Shopify product ID {shopify_product.get('id')}.")
        
        return product_template
