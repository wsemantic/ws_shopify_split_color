# -*- coding: utf-8 -*-
{
    'name': "ws_shopify_split_color",

    'summary': """
        Short (1 phrase/line) summary of the module's purpose, used as
        subtitle on modules listing or apps.openerp.com""",

    'description': """
        Long description of module's purpose
    """,

    'author': "Semantic Web Software SL",
    'website': "https://wsemantic.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/16.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Uncategorized',
    'version': '16.0.0.2',

    # any module necessary for this one to work correctly
    'depends': ['sale','pragtech_odoo_shopify_connector'], 

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        'views/shopify_instance.xml',
        'views/templates.xml',
        'wizard/operation_view.xml',
    ],
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
    "license": "AGPL-3",
}
