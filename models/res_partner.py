import json
import re
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

        _logger.info("WSSH Import customer %i ", len(shopify_instance_ids))
        for shopify_instance_id in shopify_instance_ids:
            # Construir la URL para obtener clientes
            _logger.info("WSSH dentro instance %s ", shopify_instance_id.name)
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
                        links = shopify_instance_id._parse_link_header(link_header)
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
        :return: Lista de IDs de res.partner creados o actualizados.
        """
        customer_list = []
    
        for shopify_customer in shopify_customers:
            # Reutilizamos la lógica para obtener el nombre del cliente
            name = self._get_customer_name(shopify_customer)
    
            # Procesamos la dirección
            address = shopify_customer.get('addresses')
            street = street2 = city = zip = ""
            country_id = False
            if address:
                street = shopify_customer.get('address1') or address[0].get('address1') or ""
                street2 = shopify_customer.get('address2') or address[0].get('address2') or ""
                city = shopify_customer.get('city') or address[0].get('city') or ""
                zip = shopify_customer.get('zip') or address[0].get('zip') or ""
                country_code = shopify_customer.get('country_code') or address[0].get('country_code')
                phone=shopify_customer.get('phone') or address[0].get('phone')
                country = self.env['res.country'].sudo().search([('code', '=', country_code)], limit=1)
                country_id = country.id if country else False
    
            # Se busca si el partner ya existe
            partner = self._find_existing_partner(shopify_customer, shopify_instance_id)
            
            if partner:
                _logger.info(f"WSSH Partner existente encontrado {partner.name} updatename {name} id {shopify_customer.get('id')} skip {skip_existing_customer} vat {shopify_customer.get('vat')}")
                if not skip_existing_customer:
                    _logger.info(f"WSSH Dentro skip")
                    # Construimos vals_update solo con los campos que tengan valor
                    vals_update = {}
    
                    if name:
                        vals_update['name'] = name
                    if shopify_customer.get('email'):
                        vals_update['email'] = shopify_customer.get('email')
                    if phone:
                        vals_update['phone'] = phone
                    if street:
                        vals_update['street'] = street
                    if street2:
                        vals_update['street2'] = street2
                    if city:
                        vals_update['city'] = city
                    if zip:
                        vals_update['zip'] = zip
                    if country_id:
                        vals_update['country_id'] = country_id
                    
                    # Siempre se actualizan estos campos
                    vals_update['shopify_customer_id'] = shopify_customer.get('id')
                    vals_update['is_shopify_customer'] = True
                    vals_update['vat'] = shopify_customer.get('vat')
                        
                    partner.with_context(no_vat_validation=True).write(vals_update)
            else:
                _logger.info(f"WSSH Partner NO encontrado {name} id {shopify_customer.get('id')}")
                # Se arma el diccionario completo para la creación del partner
                vals = {
                    'name': name,
                    'customer_rank': 1,
                    'email': shopify_customer.get('email'),
                    'vat': shopify_customer.get('vat'),
                    'shopify_customer_id': shopify_customer.get('id'),
                    'ref': 'SID' + str(shopify_customer.get('id')),
                    'is_shopify_customer': True,
                    'phone': phone,
                    'shopify_instance_id': shopify_instance_id.id,
                    'shopify_note': shopify_customer.get('note'),
                    'street': street,
                    'street2': street2,
                    'city': city,
                    'zip': zip,
                    'country_id': country_id,
                }
                partner = super(ResPartner, self).with_context(no_vat_validation=True).create(vals)

            
            customer_list.append(partner.id)
        
        return customer_list
    
    def _get_customer_name(self, shopify_customer):
        """
        Genera el nombre del cliente a partir de 'first_name' y 'last_name'.  
        Si no se dispone de ellos, se utiliza el email o un valor por defecto.
        """
        first = shopify_customer.get('first_name') or ''
        last = shopify_customer.get('last_name') or ''
        name = (first + ' ' + last).strip()
        if not name:
            name = shopify_customer.get('email') or _("Shopify Customer")
        return name	

    def _find_existing_partner(self, shopify_customer,shopify_instance_id):
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
        phone = shopify_customer.get('phone')
        # Limpiar cadenas para eliminar secuencias de escape no deseadas
        if email:
            email = shopify_instance_id.clean_string(email)
        if vat:
            vat = shopify_instance_id.clean_string(vat)
        if phone:
            phone = shopify_instance_id.clean_string(phone)
        # Buscar por mapping de Shopify
        partner = self.search([('shopify_customer_id', '=', shopify_customer_id)], limit=1)
        if partner:
            return partner

        # Validar email y vat antes de agregarlos al dominio de búsqueda
        if email and not self._is_valid_email(email):
            _logger.warning("El email '%s' no es válido y se omite en la búsqueda", email)
            email = None
        if vat and not self._is_valid_vat(vat):
            _logger.warning("El VAT '%s' no es válido y se omite en la búsqueda", vat)
            vat = None
        # Validar teléfono (opcional)
        if phone and not self._is_valid_phone(phone):
            _logger.warning("El teléfono '%s' no es válido y se omite en la búsqueda", phone)
            phone = None
			
        # Si no se encontró, buscar por email o VAT en partners sin mapping
        domain = [('shopify_customer_id', '=', False)]
        # Recopilamos las condiciones disponibles
        or_conditions = []
        if email:
            or_conditions.append(('email', '=', email))
        if vat:
            or_conditions.append(('vat', '=', vat))
        if phone:
            or_conditions.append(('phone', '=', phone))
    
        # Si tenemos más de una condición, combinamos con el operador OR.
        if len(or_conditions) == 1:
            domain += or_conditions
        elif len(or_conditions) > 1:
            # Para N condiciones se necesitan (N-1) operadores OR. Ejemplo:
            # Si hay 3 condiciones: ['|', '|', cond1, cond2, cond3]
            domain += ['|'] * (len(or_conditions) - 1) + or_conditions
        partner = self.search(domain, limit=1)
        return partner

    def _is_valid_email(self, email):
        """
        Valida que el email tenga un formato básico correcto.
        """
        pattern = r'^[\w\.\-\+]+@[\w\.-]+\.\w+$'
        if isinstance(email, str) and re.match(pattern, email):
            return True
        return False

    def _is_valid_vat(self, vat):
        """
        Valida que el VAT no esté vacío y contenga caracteres alfanuméricos básicos.
        Esta validación puede ajustarse según las normativas locales.
        """
        if isinstance(vat, str) and vat.strip():
            pattern = r'^[\w\-\s\.]{3,}$'
            if re.match(pattern, vat):
                return True
        return False
        
    def _is_valid_phone(self, phone):
        """
        Valida que el teléfono tenga un formato básico.
        Se permite dígitos, espacios, paréntesis, guiones y el signo '+'.
        """
        pattern = r'^[\d\+\-\s\(\)]+$'
        if isinstance(phone, str) and re.match(pattern, phone):
            return True
        return False

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
