# -*- coding: utf-8 -*-
# from odoo import http


# class Wsem(http.Controller):
#     @http.route('/wsem/wsem', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/wsem/wsem/objects', auth='public')
#     def list(self, **kw):
#         return http.request.render('wsem.listing', {
#             'root': '/wsem/wsem',
#             'objects': http.request.env['wsem.wsem'].search([]),
#         })

#     @http.route('/wsem/wsem/objects/<model("wsem.wsem"):obj>', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('wsem.object', {
#             'object': obj
#         })
