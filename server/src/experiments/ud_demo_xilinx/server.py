#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2005-2009 University of Deusto
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.
#
# This software consists of contributions made by many individuals, 
# listed below:
#
# Author: Pablo Orduña <pablo@ordunya.com>
# 

from voodoo.gen.caller_checker import caller_check
from voodoo.log import logged
from voodoo.override import Override
import experiments.ud_xilinx.server as UdXilinxExperiment
import os
import weblab.data.server_type as ServerType
import weblab.experiment.util as ExperimentUtil

import json

class UdDemoXilinxExperiment(UdXilinxExperiment.UdXilinxExperiment):

    FILES = {
            'PLD'  : 'cpld.jed',
            'FPGA' : 'fpga.bit',
        }
    
    def __init__(self, coord_address, locator, cfg_manager, *args, **kwargs):
        super(UdDemoXilinxExperiment,self).__init__(coord_address, locator, cfg_manager, *args, **kwargs)
        file_path = os.path.dirname(__file__) + os.sep + self.FILES[self._xilinx_device]
        self.file_content = ExperimentUtil.serialize(open(file_path, "rb").read())
        
    
    @Override(UdXilinxExperiment.UdXilinxExperiment)
    @caller_check(ServerType.Laboratory)
    @logged("info")
    def do_get_api(self):
        """
        Returns the API version. Unlike the Xilinx base it inherits from, this has not yet
        been ported to version 2.
        """
        return "2"
    
        
    @Override(UdXilinxExperiment.UdXilinxExperiment)
    @caller_check(ServerType.Laboratory)
    @logged("info")
    def do_start_experiment(self):
        super(UdDemoXilinxExperiment, self).do_send_file_to_device(self.file_content, "program")
        return json.dumps({ "initial_configuration" : "{ \"webcam\" : \"%s\" }" % self.webcam_url, "batch" : False })

    @Override(UdXilinxExperiment.UdXilinxExperiment)
    @caller_check(ServerType.Laboratory)
    @logged("info")
    def do_dispose(self):
        super(UdDemoXilinxExperiment, self).do_dispose()
        return "ok"

    @Override(UdXilinxExperiment.UdXilinxExperiment)
    @caller_check(ServerType.Laboratory)
    @logged("info",except_for='file_content')
    def do_send_file_to_device(self, file_content, file_info):
        return "sending file not possible in demo"

    @logged("info")
    @Override(UdXilinxExperiment.UdXilinxExperiment)
    @caller_check(ServerType.Laboratory)
    def do_send_command_to_device(self, command):
        return super(UdDemoXilinxExperiment, self).do_send_command_to_device(command)

