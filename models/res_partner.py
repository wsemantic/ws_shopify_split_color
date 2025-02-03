import json

import requests
from odoo import api, fields, models, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def import_shopify_customers(self, shopify_instance_ids, skip_existing_customer):
        """
        Extiende la importación de clientes para filtrar por fecha de creación,
        usando el campo shopify_last_date_customer_import, si está definido.
        Luego, delega la creación/actualización de clientes a la implementación original.
        """
        
        # Si no se especifican instancias, se buscan las activas.
        if not shopify_instance_ids:
            shopify_instance_ids = self.env['shopify.instance'].sudo().search([('shopify_active', '=', True)])

        _logger.info("WSSH Inport customer %i ",len(shopify_instance_ids))
        for shopify_instance_id in shopify_instance_ids:
            # Construir la URL para obtener clientes
            _logger.info("WSSH dentro instance %s ",shopify_instance_id.name)
            url = self.get_customer_url(shopify_instance_id, endpoint='customers.json')
            access_token = shopify_instance_id.shopify_shared_secret
            headers = {
                "X-Shopify-Access-Token": access_token,
            }
            # Se inicia con los parámetros básicos
            params = {
                "limit": 250,
                "page_info": None,
            }
            # Si existe shopify_last_date_customer_import (puede ser nulo la primera vez), se añade el filtro.
            if shopify_instance_id.shopify_last_date_customer_import:
                params["created_at_min"] = shopify_instance_id.shopify_last_date_customer_import

            all_customers = []
            while True:
                _logger.info("WSSH iteracion response")
                response = requests.get(url, headers=headers, params=params)
                if response.status_code == 200 and response.content:
                    shopify_customers = response.json()
                    customers = shopify_customers.get('customers', [])
                    all_customers.extend(customers)
                    _logger.info(f"WSSH iteracion response n {len(all_customers)}")
                    # Manejo de paginación: suponemos que en tu respuesta se usa page_info.
                    link_header = response.headers.get('Link')
                    if link_header:
                        links = shopify_instance._parse_link_header(link_header)
                        if 'next' in links:
                            url = links['next']
                            params = None
                            continue
                break
            _logger.info("WSSH Found %d customer to export for instance %s", len(all_customers), shopify_instance_id.name)
            
            if all_customers:
                # Aquí usamos super() para delegar en la implementación original de create_customers
                # y evitar reescribir toda la lógica de creación/actualización de clientes.
                return self.create_customers(all_customers, shopify_instance_id, skip_existing_customer)
            else:
                _logger.info("Customers not found in shopify store")
                return []
                
    def create_customers(self, shopify_customers, shopify_instance_id, skip_existing_customer):
        """
        Crea o actualiza clientes en Odoo a partir de una lista de clientes de Shopify.
        
        Primero se intenta buscar el partner por shopify_customer_id. Si no se encuentra,
        se realiza una búsqueda adicional por email o VAT en partners no mapeados.
        En caso de no encontrarlo, se crea un nuevo partner usando el método original (super).
        
        :param shopify_customers: Lista de diccionarios con datos de clientes de Shopify.
        :param shopify_instance_id: Instancia de Shopify.
        :param skip_existing_customer: Flag para omitir actualización si ya existe.
        :return: recordset de res.partner creados o actualizados.
        """

        Customer = self.env['res.partner']
        customer_list = []

        for shopify_customer in shopify_customers:            
            partner = self._find_existing_partner(shopify_customer)
            
            if partner:
                _logger.info(f"WSSH Partner existente encontrado {partner.name} id {shopify_customer.get('id')}")
                # Si se requiere actualizar los datos, se pueden incluir aquí:
                partner.write({
                    'shopify_customer_id': shopify_customer.get('id'),
                    'is_shopify_customer': True
                    # Puedes actualizar otros campos que consideres necesarios
                })
            else:
                name=((shopify_customer.get('first_name') or '') + ' ' + (shopify_customer.get('last_name') or '')).strip()
                _logger.info(f"WSSH Partner NO encontrado {name} id {shopify_customer.get('id')}")
                # Prepara los valores a partir de shopify_customer
                vals = {
                    'name': name,
                    'email': shopify_customer.get('email'),
                    'vat': shopify_customer.get('vat'),
                    'shopify_customer_id': shopify_customer.get('id'),
                    'ref':'SID'+ str(shopify_customer.get('id')),
                    'is_shopify_customer': True
                    
                    # Mapea aquí otros campos que necesites importar
                }
                # Llama al método original para crear el partner
                partner = super(ResPartner, self).create(vals)
            customer_list.append(customer.id)
        return customer_list

    def _find_existing_partner(self, shopify_customer):
        """
        Busca un partner existente en Odoo a partir de los datos del cliente de Shopify.
        
        Primero intenta encontrarlo por el ID de Shopify (almacenado en shopify_customer_id).
        Si no se encuentra, busca entre los partners sin mapping (shopify_customer_id=False)
        aquellos que coincidan por email o VAT.
        
        :param shopify_customer: Diccionario con los datos del cliente de Shopify.
        :return: recordset de res.partner (vacío si no se encuentra).
        """
        shopify_customer_id = shopify_customer.get('id')
        email = shopify_customer.get('email')
        vat = shopify_customer.get('vat')

        # Buscar por mapping de Shopify
        partner = self.search([('shopify_customer_id', '=', shopify_customer_id)], limit=1)
        if partner:
            return partner

        # Si no se encontró, buscar por email o VAT en partners sin mapping
        domain = [('shopify_customer_id', '=', False)]
        if email and vat:
            # Usamos el operador OR para buscar coincidencia en email o en vat
            domain += ['|', ('email', '=', email), ('vat', '=', vat)]
        elif email:
            domain.append(('email', '=', email))
        elif vat:
            domain.append(('vat', '=', vat))
        partner = self.search(domain, limit=1)
        return partner


    def export_customers_to_shopify(self, shopify_instance_ids, update):
        """
        Extiende la exportación de clientes para que, en caso de actualización (update=True),
        se exporten solo aquellos partners cuyo write_date sea superior al valor de
        shopify.instance.last_export_customer.
        Se filtra la lista de partners y se inyecta en el contexto (active_ids) para luego
        delegar la ejecución original mediante super().
        """
        # Obtención de los partners a exportar según la lógica original
        partner_ids = self.sudo().browse(self._context.get("active_ids"))
        if not partner_ids:
            if not update:
                partner_ids = self.sudo().search([('is_shopify_customer', '=', False), ('is_exported', '=', False)])
            else:
                partner_ids = self.sudo().search([])

        # Si se está en update y la instancia tiene definida la fecha de última exportación,
        # se filtran los partners cuyo write_date sea mayor a esa fecha.
        if update:
            filtered_partner_ids = self.env['res.partner']
            for instance in shopify_instance_ids:
                if instance.last_export_customer:
                    # Se acumulan los partners que cumplan la condición para cada instancia
                    filtered_partner_ids |= partner_ids.filtered(lambda p: p.write_date > instance.last_export_customer)
                else:
                    filtered_partner_ids |= partner_ids
            partner_ids = filtered_partner_ids
            _logger.info("Filtered partners for update: %s", partner_ids.mapped('id'))

            # Actualizamos el contexto para que el método original utilice estos partners filtrados
            self = self.with_context(active_ids=partner_ids.ids)

        # Llamamos al método original (del conector) para que realice la exportación de clientes
        result = super(ResPartner, self).export_customers_to_shopify(shopify_instance_ids, update)

        # Opcional: actualizar la fecha de exportación de clientes en cada instancia,
        # por ejemplo, al finalizar la exportación.
        for instance in shopify_instance_ids:
            instance.last_export_customer = fields.Datetime.now()

        return result
