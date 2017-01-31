# Original Copyright (c) 2014 by Ladislav Lhotka, CZ.NIC <lhotka@nic.cz>
# Copyright (c) 2016 by Puppet, Inc.
#
# Pyang plugin generating Puppet 3.x Type and Provider..
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

"""allpuppet output plugin

This plugin takes a YANG data model and generates an XML instance
document containing sample elements for all data nodes.

* An element is present for every leaf, container or anyxml.

* At least one element is present for every leaf-list or list. The
  number of entries in the sample is min(1, min-elements).

* For a choice node, sample element(s) are present for each case.

* Leaf, leaf-list and anyxml elements are empty (exception:
  --allpuppet-defaults option).
"""

import os
import sys
import optparse
import copy
import pdb
import StringIO
import shutil

from lxml import etree as ET
from pyang import plugin, statements, error
from pyang.util import dictsearch, unique_prefixes


def pyang_plugin_init():
    plugin.register_plugin(AllPuppetPlugin())


class AllPuppetPlugin(plugin.PyangPlugin):

    def add_opts(self, optparser):
        optlist = [
            optparse.make_option("--allpuppet-doctype",
                                 dest="doctype",
                                 default="data",
                                 help="Type of sample XML document " +
                                 "(data or config)."),
            optparse.make_option("--allpuppet-output-format",
                                 dest="output_format",
                                 default="all",
                                 help="one of type/self_instances/flush/test/all"),
            optparse.make_option("--allpuppet-naming-seed",
                                 dest="naming_seed",
                                 default="",
                                 help="what is the starting point for the attribute name"),
            optparse.make_option("--allpuppet-defaults",
                                 action="store_true",
                                 dest="sample_defaults",
                                 default=False,
                                 help="Insert leafs with defaults values."),
            optparse.make_option("--allpuppet-annotations",
                                 action="store_true",
                                 dest="sample_annots",
                                 default=False,
                                 help="Add annotations as XML comments."),
            optparse.make_option("--allpuppet-path",
                                 dest="sample_path",
                                 help="Subtree to print"),
            optparse.make_option("--module-name",
                                 dest="module_name",
                                 help="Name of Puppet Module"),
            optparse.make_option("--pcore-path",
                                 dest="pcore_path",
                                 help="Folder to output pcore files"),
            optparse.make_option("--pcore-name",
                                 dest="pcore_name",
                                 help="Name of pcore TypeSet"),
        ]
        g = optparser.add_option_group(
            "allpuppet output specific options")
        g.add_options(optlist)

    def add_output_format(self, fmts):
        self.multiple_modules = True
        fmts['allpuppet'] = self

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        """Main control function.

        Set up the top-level parts of the sample document, then process
        recursively all nodes in all data trees, and finally emit the
        sample XML document.
        """
        if ctx.opts.sample_path is not None:
            path = ctx.opts.sample_path.split('/')
            if path[0] == '':
                path = path[1:]
        else:
            path = []

        for (epos, etag, eargs) in ctx.errors:
            if error.is_error(error.err_level(etag)):
                raise error.EmitError(
                    "allpuppet plugin needs a valid module")
        self.doctype = ctx.opts.doctype
        if self.doctype not in ("config", "data"):
            raise error.EmitError("Unsupported document type: %s" %
                                  self.doctype)
        self.output_format = ctx.opts.output_format
        self.naming_seed = ctx.opts.naming_seed
        self.annots = ctx.opts.sample_annots
        self.defaults = ctx.opts.sample_defaults
        self.fd = fd
        #self.type_writer = StringIO.StringIO()
        self.pcore_types = []
        # Attempt to setup the Puppet module name and Pcore type info
        self.module_name = ctx.opts.module_name.capitalize() if ctx.opts.module_name else "Vanilla_ice"
        self.pcore_name = ctx.opts.pcore_name
        if self.pcore_name:
            self.module_name = self.module_name + "::" + self.pcore_name.capitalize()
        self.node_handler = {
            "container": self.container,
            "leaf": self.leaf,
            "anyxml": self.anyxml,
            #"choice": self.choice,
            "choice": self.process_children,
            "case": self.process_children,
            "list": self.list,
            "leaf-list": self.leaf_list,
            "rpc": self.ignore,
            "notification": self.ignore
        }
        # Map built in YANG primatives to Puppet
        self.yang_type = {
            "int8": "Integer",
            "int16": "Integer",
            "int32": "Integer",
            "int64": "Integer",
            "uint8": "Integer",
            "uint16": "Integer",
            "uint32": "Integer",
            "uint64": "Integer",
            "decimal64": "Float",
            "boolean": "Boolean"
        }
        # Find the namespaces for all modules, not just top level
        self.ns_uri = {statement: statement.search_one("namespace").arg for statement, prefix in unique_prefixes(ctx).iteritems()}
        # Build dictionary of all namespaces by prefix
        self.nsmap = {unique_prefixes(ctx)[k]: v for k, v in self.ns_uri.iteritems()}
        # nsmap[None] = "urn:ietf:params:xml:ns:netconf:base:1.0"
        self.top = ET.Element(self.doctype,
                              {"xmlns": "urn:ietf:params:xml:ns:netconf:base:1.0"})
        self.tree = ET.ElementTree(self.top)

        # Get the list of namespaces and their prefix
        self.namespace_hash = ["'{0}' => '{1}'".format(k, v) for k, v in self.nsmap.iteritems()]
        #pdb.set_trace()
        for yam in modules:
            self.process_children(yam, self.top, None, path)

        if self.output_format in ("flush", "all"):
            print '\n**Flush XML Template**'
            if sys.version > "3":
                self.tree.write(fd, encoding="unicode", xml_declaration=True)
            elif sys.version > "2.7":
                self.tree.write(fd, encoding="UTF-8", xml_declaration=True)
            else:
                self.tree.write(fd, encoding="UTF-8")

            # Output the ruby hash of namespaces
            print '\n\n**Flush namespace hash**'
            print '{ ' + ', '.join(x for x in self.namespace_hash) + ' }'

        elif self.output_format in ("type", "all"):
            self.fd.write("\n\n**pcore types**\n")
            for type_writer in self.pcore_types:
                if ctx.opts.pcore_path:
                    directory = ctx.opts.pcore_path + '/' + self.pcore_name if self.pcore_name else ctx.opts.pcore_path
                    if not os.path.exists(directory):
                        os.makedirs(directory)
                    fname = "{0}/{1}.pp".format(directory, type_writer[0])
                    with open (fname, 'w') as of:
                        buf = type_writer[1]
                        buf.seek (0)
                        shutil.copyfileobj (buf, of)
                self.fd.write("{0}\n".format(type_writer[1].getvalue()))

    def ignore(self, node, elem, module, path, mode=None, type_writer=None):
        """Do nothing for `node`."""
        pass

    def process_children(self, node, elem, module, path, mode=None, type_writer=None):
        """Proceed with all children of `node`."""
        for ch in node.i_children:
            #self.fd.write("process_children raw_keyword  {0}\n".format(ch.raw_keyword))
            if ch.i_config or self.doctype == "data":
                self.node_handler[ch.keyword](ch, elem, module, path, mode=mode, type_writer=type_writer)

    def container(self, node, elem, module, path, mode=None, type_writer=None):
        """Create a sample container element and proceed with its children."""
        # pdb.set_trace()
        nel, newm, path = self.sample_element(node, elem, module, path)
        node_name = nel.tag.replace('-', '_')
        # If this is a top level container it should be a Puppet resource type
        if elem.tag == "data":
            if self.output_format in ("type", "all"):
                self.fd.write("""Puppet::Type.newtype(:{0}) do\n""".format(node_name))
            if path is None:
                return
            self.process_children(node, nel, newm, path, mode=mode, type_writer=type_writer)
        else:
            if type_writer:
                type_writer.write("    {0} => Optional[{1}::{2}],\n".format(node_name, self.module_name, node_name.capitalize()))
            type_writer = StringIO.StringIO()
            type_writer.write('''type {0}::{1} = Object[{{
  attributes => {{\n'''.format(self.module_name, node_name.capitalize()))
            if path is None:
                type_writer.write("    xmlns => {{type => String, value => \"{0}\", kind => constant}},\n".format(nel.attrib['xmlns']))
                type_writer.write("}}]\n")
                self.pcore_types.append((node_name, type_writer))
                return
            if self.annots:
                pres = node.search_one("presence")
                if pres is not None:
                    nel.append(ET.Comment(" presence: %s " % pres.arg))
            self.process_children(node, nel, newm, path, mode=mode, type_writer=type_writer)
            #pdb.set_trace()
            type_writer.write("    xmlns => {{type => String, value => \"{0}\", kind => constant}},\n".format(nel.attrib['xmlns']))
            type_writer.write("}}]\n")
            self.pcore_types.append((node_name, type_writer))

    def leaf(self, node, elem, module, path, mode=None, type_writer=None):
        """Create a sample leaf element."""
        if self.naming_seed == '':
            attribute_name = node.arg
            try:
                description = node.search_one('description').arg
            except:
                description = ''

            if node.search_one('type').arg.lower() == 'boolean':
                if self.output_format in ("self_instances", "all"):
                    xpath = "variable.xpath(\"{0}/{1}\").text.downcase == 'true' ? true : nil".format(self.tree.getpath(elem), node.arg)
                if self.output_format in ("type", "all"):
                    self.puppet_type(node.arg, description, ptype='boolean', mode=mode, type_writer=type_writer)
            elif node.search_one('type').arg.lower() == 'empty':
                if self.output_format in ("self_instances", "all"):
                    xpath = "!(variable.xpath(\"{0}/{1}\").empty?) ? true : nil".format(self.tree.getpath(elem), node.arg)
                if self.output_format in ("type", "all"):
                    self.puppet_type(node.arg, description, ptype='boolean', mode=mode, type_writer=type_writer)
            else:
                if self.output_format in ("self_instances", "all"):
                    xpath = "variable.xpath(\"{0}/{1}\").text".format(self.tree.getpath(elem), node.arg)
                if self.output_format in ("type", "all"):
                    ptype = node.search_one('type').arg.lower() or None
                    self.puppet_type(node.arg, description, ptype=ptype, mode=mode, type_writer=type_writer)

        else:
            if type_writer:
                try:
                    prefix = elem.tag.replace('/', '_')
                except IndexError:
                    prefix = ''
            else:
                try:
                    prefix = self.tree.getpath(elem).split(self.naming_seed)[1].replace('/', '_')
                except IndexError:
                    prefix = ''
            if prefix != '':
                #pdb.set_trace()
                # we are nested
                attribute_name = (prefix + "_" + node.arg)
                # remove leading underscore
                attribute_name = attribute_name.lstrip('_')
            else:
                # we are not nested
                attribute_name = node.arg
            try:
                description = node.search_one('description').arg
            except:
                description = ''

            if self.output_format in ("type", "all"):
                # Throwing in some super hack to omit name seeds in pcore
                #attribute_name = node.arg if type_writer else attribute_name
                ptype = node.search_one('type').arg.lower() or None
                self.puppet_type(attribute_name, description, ptype=ptype, mode=mode, type_writer=type_writer)

            if self.output_format in ("self_instances", "all"):
                try:
                    actual_xpath = self.tree.getpath(elem).split(self.naming_seed)[1]
                except IndexError:
                    actual_xpath = ''

                if actual_xpath != '':
                    actual_xpath = "./" + self.tree.getpath(elem).split(self.naming_seed)[1] + "/" + node.arg
                else:
                    actual_xpath = node.arg

                if node.search_one('type').arg.lower() == 'boolean':
                    xpath = "variable.xpath(\"{0}\").text.downcase == 'true' ? true : nil".format(actual_xpath)
                elif node.search_one('type').arg.lower() == 'empty':
                    xpath = "!(variable.xpath(\"{0}\").empty?) ? true : nil".format(actual_xpath)
                else:
                    xpath = "variable.xpath(\"{0}\").text".format(actual_xpath)

        if self.output_format in ("flush", "all"):
            # We first need to work with the node itself, we do not know the namespace yet
            mm = node.main_module()
            if mm != module:
                node_ns = self.ns_uri[mm]
                module = mm
            # else:
            #     # Check if node is extending a base identiy, if so use base namespace
            #     try:
            #         identity_prefix = node.search_one('type').search_one('base').arg.split(':')[0]
            #         prefix_uri = dictsearch(identity_prefix, unique_prefixes(mm.i_ctx))
            #         node_ns = self.ns_uri[prefix_uri]
            #     except:
            #         node_ns = self.ns_uri[module]
            node_ns = self.ns_uri[module]

            # Set the node itself - this is outermost part of xpath
            ns_object = dictsearch(node_ns, self.ns_uri)
            name_spaced_path = unique_prefixes(node.main_module().i_ctx)[ns_object] + ':' + node.arg
            # Set the immediate parent node
            ns_object = dictsearch(elem.attrib['xmlns'], self.ns_uri)
            name_spaced_path = unique_prefixes(node.main_module().i_ctx)[ns_object] + ':' + elem.tag + '/' + name_spaced_path
            # print name_spaced_path

            # Walk backwards for ancestors
            for parent in elem.iterancestors():
                # We do not want the document top level
                # pdb.set_trace()
                if parent.attrib['xmlns'] == 'urn:ietf:params:xml:ns:netconf:base:1.0':
                    continue
                else:
                    # namespace of parent object
                    ns_object = dictsearch(parent.attrib['xmlns'], self.ns_uri)
                    # Add parent node and namespace to xpath
                    name_spaced_path = unique_prefixes(node.main_module().i_ctx)[ns_object] + ':' + parent.tag + '/' + name_spaced_path

            # Write out the flush xpaths
            self.puppet_flush(attribute_name, name_spaced_path)

        if self.output_format in ("self_instances", "all"):
            # Write the instance
            self.puppet_instance(attribute_name, xpath)

        if node.i_default is None:
            nel, newm, path = self.sample_element(node, elem, module, path)
            if path is None:
                return
            if self.annots:
                nel.append(ET.Comment(" type: %s " % node.search_one("type").arg))
        elif self.defaults:
            nel, newm, path = self.sample_element(node, elem, module, path)
            if path is None:
                return
            nel.text = str(node.i_default)

    def anyxml(self, node, elem, module, path, mode=None, type_writer=None):
        """Create a sample anyxml element."""
        nel, newm, path = self.sample_element(node, elem, module, path)
        if path is None:
            return
        if self.annots:
            nel.append(ET.Comment(" anyxml "))

    def list(self, node, elem, module, path, mode='pcore', type_writer=None):
        """Create sample entries of a list."""
        nel, newm, path = self.sample_element(node, elem, module, path)
        if path is None:
            return
        node_name = node.arg.replace('-', '_')
        if type_writer:
            type_writer.write("    {0} => Optional[Array[{1}::{2}]],\n".format(node_name, self.module_name, node_name.capitalize()))
        else:
            try:
                description = node.search_one('description').arg
            except:
                description = ''
            self.fd.write("""  newproperty(:{0}, :array_matching => :all) do
    desc '{1}'
  end \n""".format(node_name, description.replace('\n', ' ').replace("\'", "\\'")))
        type_writer = StringIO.StringIO()
        #pdb.set_trace()
        type_writer.write('''type {0}::{1} = Object[{{
  attributes => {{\n'''.format(self.module_name, node_name.capitalize()))
        self.process_children(node, nel, newm, path, mode='pcore', type_writer=type_writer)
        minel = node.search_one("min-elements")
        self.add_copies(node, elem, nel, minel)
        self.list_comment(node, nel, minel)
        type_writer.write("    xmlns => {{type => String, value => \"{0}\", kind => constant}},\n".format(nel.attrib['xmlns']))
        type_writer.write("}}]\n")
        self.pcore_types.append((node_name, type_writer))

    def choice(self, node, elem, module, path, mode='pcore', type_writer=None):
        """Create sample entries of a list."""
        nel, newm, path = self.sample_element(node, elem, module, path)
        if path is None:
            return
        node_name = node.arg.replace('-', '_')
        self.fd.write("choice node_name {0}\n".format(node_name))
        if type_writer:
            type_writer.write("    {0} => Optional[Array[{1}::{2}]],\n".format(node_name, self.module_name, node_name.capitalize()))
        else:
            try:
                description = node.search_one('description').arg
            except:
                description = ''
            self.fd.write("""  newproperty(:{0}, :array_matching => :all) do
    desc '{1}'
  end \n""".format(node_name, description.replace('\n', ' ').replace("\'", "\\'")))
        type_writer = StringIO.StringIO()
        #pdb.set_trace()
        type_writer.write('''type {0}::{1} = Object[{{
  attributes => {{\n'''.format(self.module_name, node_name.capitalize()))
        self.process_children(node, nel, newm, path, mode='pcore', type_writer=type_writer)
        minel = node.search_one("min-elements")
        self.add_copies(node, elem, nel, minel)
        self.list_comment(node, nel, minel)
        type_writer.write("    xmlns => {{type => String, value => \"{0}\", kind => constant}},\n".format(nel.attrib['xmlns']))
        type_writer.write("}}]\n")
        self.pcore_types.append((node_name, type_writer))

    def leaf_list(self, node, elem, module, path, mode=None, type_writer=None):
        """Create sample entries of a leaf-list."""
        nel, newm, path = self.sample_element(node, elem, module, path)
        if path is None:
            return
        #node_name = node.arg.replace('-', '_')
        #self.fd.write("    {0} => Optional[Array[Vanilla_ice::{1}]]\n".format(node_name, node_name.capitalize()))
        #self.type_writer.write('''type Vanilla_ice::{0} = Object[{{
  #attributes => {{\n'''.format(node_name.capitalize()))
        minel = node.search_one("min-elements")
        self.add_copies(node, elem, nel, minel)
        self.list_comment(node, nel, minel)
        #self.type_writer.write("}}]\n")

    def sample_element(self, node, parent, module, path, mode=None, type_writer=None):
        """Create element under `parent`.

        Declare new namespace if necessary.
        """
        if path is None:
            return parent, module, None
        elif path == []:
            # GO ON
            pass
        else:
            if node.arg == path[0]:
                path = path[1:]
            else:
                return parent, module, None

        res = ET.SubElement(parent, node.arg, nsmap=self.nsmap)
        mm = node.main_module()
        if mm != module:
            res.attrib["xmlns"] = self.ns_uri[mm]
            module = mm
        # else:
        #     # Check if node is extending a base identiy, if so use base namespace
        #     try:
        #         identity_prefix = node.search_one('type').search_one('base').arg.split(':')[0]
        #         prefix_uri = dictsearch(identity_prefix, unique_prefixes(mm.i_ctx))
        #         res.attrib["xmlns"] = self.ns_uri[prefix_uri]
        #     except:
        #         # pdb.set_trace()
        #         res.attrib["xmlns"] = self.ns_uri[module]
        res.attrib["xmlns"] = self.ns_uri[module]
        return res, module, path

    def add_copies(self, node, parent, elem, minel):
        """Add appropriate number of `elem` copies to `parent`."""
        rep = 0 if minel is None else int(minel.arg) - 1
        for i in range(rep):
            parent.append(copy.deepcopy(elem))

    def list_comment(self, node, elem, minel):
        """Add list annotation to `elem`."""
        if not self.annots:
            return
        lo = "0" if minel is None else minel.arg
        maxel = node.search_one("max-elements")
        hi = "" if maxel is None else maxel.arg
        elem.insert(0, ET.Comment(" # entries: %s..%s " % (lo, hi)))

    def puppet_type(self, pname, description, ptype=None, mode=None, type_writer=None):
        try:
            ptype = self.yang_type[ptype]
        except:
            ptype = 'String'
        pname = pname.replace('-', '_').lower()
        description = description.replace('\n', ' ').replace("\'", "\\'")
        if type_writer:
            type_writer.write("    {0} => Optional[{1}],\n".format(pname, ptype))
        else:
            self.fd.write("""  newproperty(:{0}) do
        desc '{1}'
      end\n""".format(pname, description))

    def puppet_instance(self, pname, xpath):
        provider = ":{0}  => ".format(pname.replace('-', '_'))
        self.fd.write(provider + xpath + ",\n")

    def puppet_flush(self, pname, xpath):
        pname = pname.replace('-', '_')
        self.fd.write("\"" + pname + "\" => \"" + xpath + "\",\n")
