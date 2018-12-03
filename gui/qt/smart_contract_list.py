#!/usr/bin/env python
#
# -*- coding: utf-8 -*-
"""
__author__ = 'CodeFace'
"""
from electrum.i18n import _
from electrum.util import block_explorer_URL, open_browser
from electrum.plugins import run_hook
from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import QAbstractItemView, QMenu, QTreeWidgetItem
from .util import MyTreeWidget,MessageBoxMixin


class SmartContractList(MyTreeWidget,MessageBoxMixin):
    filter_columns = [0, 1]

    def __init__(self, parent):
        MyTreeWidget.__init__(self, parent, self.create_menu, [_('Name'), _('Address'), _('Type')], 1, [0])
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.header().customContextMenuRequested.connect(self.create_menu_title)
        self.setSortingEnabled(True)
        self.tab_name = "Smart Contract"

    def on_doubleclick(self, item, column):
        address = item.data(0, Qt.UserRole)
        contractType = item.data(1, Qt.UserRole)
        self.parent.contract_func_dialog(self, address, contractType)

    def create_menu_title(self, position):
        menu = QMenu()

        menu.addAction(_("Add contract"), lambda: self.parent.contract_add_dialog())
        menu.addAction(_("Create contract"), lambda: self.parent.contract_create_dialog())

        run_hook('create_smart_contract_menu', menu, [])
        menu.exec_(self.viewport().mapToGlobal(position))

    def create_menu(self, position):
        menu = QMenu()
        selected = self.selectedItems()
        multi_select = len(selected) > 1
        if not selected:
            menu.addAction(_("Add contract"), lambda: self.parent.contract_add_dialog())
            menu.addAction(_("Create contract"), lambda: self.parent.contract_create_dialog())
        elif not multi_select:
            item = selected[0]
            # name = item.text(0)
            address = item.text(1)
            column = self.currentColumn()
            column_title = self.headerItem().text(column)
            column_data = '\n'.join([item.text(column) for item in selected])
            menu.addAction(_("Copy %s") % column_title, lambda: self.parent.app.clipboard().setText(column_data))
            menu.addAction(_("Edit"), lambda: self.parent.contract_edit_dialog(address))
            menu.addAction(_("Function"), lambda: self.parent.contract_func_dialog(address,self))
            menu.addAction(_("Delete"), lambda: self.parent.delete_samart_contact(address))
        run_hook('create_smart_contract_menu', menu, selected)
        menu.exec_(self.viewport().mapToGlobal(position))

    def isTokenContract(self, abis):
        tokenAbis = ['init_token', 'transfer', 'balanceOf', 'transferFrom']
        matchedAbiCount = 0
        for abi in abis:
            try:
                if tokenAbis.index(abi['name']) >= 0:
                    matchedAbiCount += 1
            except:
                continue
        if matchedAbiCount == len(tokenAbis):
            return True
        else:
            return False

    def on_update(self):
        item = self.currentItem()
        current_key = item.data(0, Qt.UserRole) if item else None
        self.clear()
        for address in sorted(self.parent.smart_contracts.keys()):
            name, abi = self.parent.smart_contracts[address]
            if self.isTokenContract(abi):
                contractType = 'Token'
            else:
                contractType = 'Common'
            item = QTreeWidgetItem([name, address, contractType])
            item.setData(0, Qt.UserRole, address)
            item.setData(1, Qt.UserRole, contractType)
            self.addTopLevelItem(item)
            if address == current_key:
                self.setCurrentItem(item)
        run_hook('update_smart_contract_tab', self)
