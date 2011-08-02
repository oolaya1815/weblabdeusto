#!/usr/bin/env python
#-*-*- encoding: utf-8 -*-*-
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
#         Luis Rodriguez <luis.rodriguez@opendeusto.es>
# 

import time as time_module
import hashlib

import weblab.data.ServerType as ServerType
import weblab.data.Command as Command

from voodoo.cache import cache
import voodoo.ResourceManager as ResourceManager
import voodoo.log as log

import weblab.exceptions.user_processing.UserProcessingExceptions as UserProcessingExceptions
import weblab.exceptions.user_processing.CoordinatorExceptions as CoordExc
import weblab.user_processing.Reservation as Reservation
import weblab.user_processing.coordinator.WebLabQueueStatus as WebLabQueueStatus
import weblab.exceptions.laboratory.LaboratoryExceptions as LaboratoryExceptions

import weblab.experiment.Util as ExperimentUtil
import weblab.data.experiments.Usage as Usage

import threading

_resource_manager = ResourceManager.CancelAndJoinResourceManager("UserProcessor")

#TODO: configuration
LIST_EXPERIMENTS_CACHE_TIME     = 15  # seconds
GET_GROUPS_CACHE_TIME           = 15  # seconds
GET_ROLES_CACHE_TIME            = 15  # seconds
GET_USERS_CACHE_TIME            = 15  # seconds
GET_EXPERIMENTS_CACHE_TIME      = 15  # seconds
GET_EXPERIMENT_USES_CACHE_TIME  = 15  # seconds
GET_USER_INFORMATION_CACHE_TIME = 200 # seconds
GET_USER_PERMISSIONS_CACHE_TIME = 200 # seconds
DEFAULT_EXPERIMENT_POLL_TIME    = 300  # seconds
EXPERIMENT_POLL_TIME            = 'core_experiment_poll_time'

@cache(LIST_EXPERIMENTS_CACHE_TIME, _resource_manager)
def list_experiments(db_manager, db_session_id):
    return db_manager.get_available_experiments(db_session_id)

@cache(GET_USER_INFORMATION_CACHE_TIME, _resource_manager)
def get_user_information(db_manager, db_session_id):
    return db_manager.retrieve_user_information(db_session_id)

@cache(GET_GROUPS_CACHE_TIME, _resource_manager)
def get_groups(db_manager, db_session_id, parent_id):
    return db_manager.get_groups(db_session_id, parent_id)

@cache(GET_ROLES_CACHE_TIME, _resource_manager)
def get_roles(db_manager, db_session_id):
    return db_manager.get_roles(db_session_id)

@cache(GET_USERS_CACHE_TIME, _resource_manager)
def get_users(db_manager, db_session_id):
    """ Retrieves the users from the database, through the database manager. """
    return db_manager.get_users(db_session_id)

@cache(GET_EXPERIMENTS_CACHE_TIME, _resource_manager)
def get_experiments(db_manager, db_session_id):
    return db_manager.get_experiments(db_session_id)

@cache(GET_EXPERIMENT_USES_CACHE_TIME, _resource_manager)
def get_experiment_uses(db_manager, db_session_id, from_date, to_date, group_id, experiment_id, start_row, end_row, sort_by):
    return db_manager.get_experiment_uses(db_session_id, from_date, to_date, group_id, experiment_id, start_row, end_row, sort_by)

@cache(GET_USER_PERMISSIONS_CACHE_TIME, _resource_manager)
def get_user_permissions(db_manager, db_session_id):
    return db_manager.get_user_permissions(db_session_id)


class UserProcessor(object):
    """
    User processors are linked to specific sessions. Requests that arrive to the
    UserProcessingManager will be told apart through their session_id and forwarded
    to the appropriate UserProcessor.
    """

    EXPIRATION_TIME_NOT_SET=-1234

    def __init__(self, locator, session, cfg_manager, coordinator, db_manager):
        self._locator               = locator
        self._session               = session
        self._cfg_manager           = cfg_manager
        self._coordinator           = coordinator
        self._db_manager            = db_manager
        self.time_module            = time_module
        
        # The response to asynchronous commands is not immediately available, so we need to 
        # use this map to store the ids of the usage objects (commands sent), identified through 
        # their request_ids (which are not the same). As their responses become available, we will
        # use the request_ids to find the ids of the usage objects, and update them.
        # 
        # It seems that the UserProcessor is re-created rather often, so we cannot store
        # usage-related information locally. We will store it in the session object instead.
        # TODO: As of now, if the async_commands_ids is not in session we will initialize it.
        # Probably that initialization should be moved to wherever session is initialized.
        if(not self._session.has_key("async_commands_ids")):
            self._session["async_commands_ids"] = {}

    def list_experiments(self):
        db_session_id         = self._session['db_session_id']
        return list_experiments(self._db_manager, db_session_id)

    def get_user_information(self):
        db_session_id               = self._session['db_session_id']
        user_information            = get_user_information(self._db_manager, db_session_id)
        self._session['user_information'] = user_information
        return user_information

    def get_session(self):
        return self._session

    def get_session_id(self):
        return self._session['session_id']

    # 
    # Experiments
    # 

    def reserve_experiment(self, experiment_id, client_address):

        experiments = [ 
                exp for exp in self.list_experiments()
                if exp.experiment.name           == experiment_id.exp_name 
                and exp.experiment.category.name == experiment_id.cat_name
            ]

        if len(experiments) == 0:
            raise UserProcessingExceptions.UnknownExperimentIdException(
                    "User can't access that experiment (or that experiment type does not exist)"
            )

        user_information = self.get_user_information()
        if user_information.login == 'demo':
            priority = 10 # TODO: this should be part of experiment_allowed
        else:
            priority = 5 # TODO: this should be part of experiment_allowed

        client_initial_data = None # TODO: this must be passed by the client
        experiment_allowed = experiments[0]

        try:
            status, reservation_id    = self._coordinator.reserve_experiment(
                    experiment_allowed.experiment.to_experiment_id(), 
                    experiment_allowed.time_allowed, 
                    priority,
                    client_initial_data
                )
        except CoordExc.ExperimentNotFoundException:
            raise UserProcessingExceptions.NoAvailableExperimentFoundException(
                "No experiment of type <%s,%s> is currently deployed" % (
                        experiment_id.exp_name, 
                        experiment_id.cat_name
                    )
            )

        self._session['reservation_id']   = reservation_id
            
        self._initialize_polling()
        self._session['experiment_usage'] = Usage.ExperimentUsage()
        self._session['experiment_usage'].experiment_id = experiment_id
        self._session['experiment_usage'].from_ip       = client_address.client_address

        if status.status == WebLabQueueStatus.WebLabQueueStatus.RESERVED:
            self._process_reserved_status(status)

        return Reservation.Reservation.translate_reservation(
                    status
                )

    def get_reservation_status(self):
        if self._session.has_key('reservation_id'):
            reservation_id = self._session['reservation_id']
            try:
                status = self._coordinator.get_reservation_status(reservation_id)
            except CoordExc.ExpiredSessionException:
                self._session.pop('reservation_id')
            else:
                if status.status == WebLabQueueStatus.WebLabQueueStatus.RESERVED:
                    self._process_reserved_status(status)

                return Reservation.Reservation.translate_reservation(
                        status
                    )
        else:
            pass # TODO

    def _process_reserved_status(self, status):
        self._session['lab_session_id'] = status.lab_session_id
        self._session['lab_coordaddr']  = status.coord_address
        self._session['experiment_time_left'] = self.time_module.time() + status.time
        self._renew_expiration_time(
                self.time_module.time() + status.time
            )
        self._session['experiment_usage'].start_date    = self._utc_timestamp()

        lab_server        = self._locator.get_server_from_coordaddr(
                    status.coord_address,
                    ServerType.Laboratory
                )
        self._session['experiment_usage'].coord_address = lab_server.resolve_experiment_address(status.lab_session_id)


    def finished_experiment(self):
        """
        Called when the experiment ends, regardless of the way. (That is, it does not matter whether the user finished
        it explicitly or not).
        """
        error = self._finish_reservation()
        self._stop_polling()
        
        # Within the session object, we log the usage the user did. That is, the commands the user sent
        # during his session. Now that the experiment has finished, we will store these records (which include
        # the responses) within the database.
        # TODO: Note: At the moment, there is no way to guarantee that when the experiment finishes every
        # asynchronous command has also finished. Hence, besides some responses being potentially not available,
        # it would not be unlikely for some unexpected behaviour to occur under certain circumstances.
        if self._session.has_key('experiment_usage') and self._session['experiment_usage'] != None:
            experiment_usage = self._session.pop('experiment_usage')
            if experiment_usage.start_date is not None:
                experiment_usage.end_date = self._utc_timestamp()

                self._db_manager.store_experiment_usage(
                        self._session['db_session_id'],
                        experiment_usage
                    )

        if error is not None:
            raise UserProcessingExceptions.FailedToFreeReservationException(
                    "There was an error freeing reservation: %s" % error
                )

    def logout(self):
        if self._session.has_key('lab_session_id'): # TODO: improve this
            self.finished_experiment()

    def send_file(self, file_content, file_info ):
        if self._session.has_key('lab_session_id') and self._session.has_key('lab_coordaddr'):
            laboratory_server = self._locator.get_server_from_coordaddr(
                    self._session['lab_coordaddr'],
                    ServerType.Laboratory
                )

            usage_file_sent = self._store_file(file_content, file_info)
            try:
                file_sent_id = self._session['experiment_usage'].append_file(usage_file_sent)
                response = laboratory_server.send_file(
                        self._session['lab_session_id'],
                        file_content,
                        file_info
                    )

                usage_file_sent.response        = response
                usage_file_sent.timestamp_after = self._utc_timestamp()
                self._session['experiment_usage'].update_file(file_sent_id, usage_file_sent)
                return response
            except LaboratoryExceptions.SessionNotFoundInLaboratoryServerException:
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass
                raise UserProcessingExceptions.NoCurrentReservationException(
                    'Experiment reservation expired'
                )
            except LaboratoryExceptions.FailedToSendFileException, ftspe:
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass
                raise UserProcessingExceptions.FailedToSendFileException(
                        "Failed to send file: %s" % ftspe
                    )
        else:
            pass # TODO

    def send_command(self, command):
        if self._session.has_key('lab_session_id') and self._session.has_key('lab_coordaddr'):
            laboratory_server = self._locator.get_server_from_coordaddr(
                    self._session['lab_coordaddr'],
                    ServerType.Laboratory
                )
            command_id_pack = self._append_command(command)
            try:
                
                # We call the laboratory server's send_command, which will finally
                # get the command to be handled by the experiment.
                response = laboratory_server.send_command(
                        self._session['lab_session_id'],
                        command
                    )

                # The previous call was executed synchronously and we have
                # received the response. Before returning it, we will store it
                # locally so that we can log it.
                self._update_command(command_id_pack, response)

                return response
            
            except LaboratoryExceptions.SessionNotFoundInLaboratoryServerException:
                self._update_command(command_id_pack, Command.Command("ERROR: SessionNotFound: None"))
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass
                raise UserProcessingExceptions.NoCurrentReservationException(
                    'Experiment reservation expired'
                )
            except LaboratoryExceptions.FailedToSendCommandException, ftspe:
                self._update_command(command_id_pack, Command.Command("ERROR: " + str(ftspe)))
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass

                raise UserProcessingExceptions.FailedToSendCommandException(
                        "Failed to send command: %s" % ftspe
                    )
        else:
            pass # TODO
        

    def send_async_file(self, file_content, file_info ):
        """
        Sends a file asynchronously. Status of the request may be checked through
        check_async_command_status.
        
        @param file_content: Content of the file being sent
        @param file_info: File information of the file being sent
        @see check_async_command_status
        """
        
        if self._session.has_key('lab_session_id') and self._session.has_key('lab_coordaddr'):
            laboratory_server = self._locator.get_server_from_coordaddr(
                    self._session['lab_coordaddr'],
                    ServerType.Laboratory
                )

            usage_file_sent = self._store_file(file_content, file_info)
            try:
                file_sent_id = self._session['experiment_usage'].append_file(usage_file_sent)
                response = laboratory_server.send_async_file(
                        self._session['lab_session_id'],
                        file_content,
                        file_info
                    )

                usage_file_sent.response        = response
                usage_file_sent.timestamp_after = self._utc_timestamp()
                self._session['experiment_usage'].update_file(file_sent_id, usage_file_sent)
                return response
            except LaboratoryExceptions.SessionNotFoundInLaboratoryServerException:
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass
                raise UserProcessingExceptions.NoCurrentReservationException(
                    'Experiment reservation expired'
                )
            except LaboratoryExceptions.FailedToSendFileException, ftspe:
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass
                raise UserProcessingExceptions.FailedToSendFileException(
                        "Failed to send file: %s" % ftspe
                    )
        else:
            pass # TODO
        

    def check_async_command_status(self, request_identifiers):
        """ 
        Checks the status of several asynchronous commands. The request will be
        internally forwarded to the lab server. Standard async commands
        and file_send commands are treated in the same way. 
        Commands reported as finished (either successfully or not) will be
        removed, so check_async_command_status should not be called on them again.
        Before removing the commands, it will also register their response for
        logging purposes.
        
        @param request_identifiers: List of the identifiers to check
        @return: Dictionary by request-id of tuples: (status, content)
        """
        
        # Try to locate the lab server
        if self._session.has_key('lab_session_id') and self._session.has_key('lab_coordaddr'):
            laboratory_server = self._locator.get_server_from_coordaddr(
                    self._session['lab_coordaddr'],
                    ServerType.Laboratory
                )
            
            try:
                response = laboratory_server.check_async_command_status(
                        self._session['lab_session_id'],
                        request_identifiers
                    )

                # Within the response map, we might now have the real response to one
                # (or more) async commands. We will update the usage object of the
                # command with its response, so that once the experiment ends it appears
                # in the log as expected.
                for req_id, (cmd_status, cmd_response) in response.items(): #@UnusedVariable
                    if(req_id in self._session["async_commands_ids"]):
                        usage_obj_id = self._session["async_commands_ids"][req_id]
                        # TODO: Bug here. async_commands_ids is empty.
                        self._update_command(usage_obj_id, cmd_response)

                return response

            except LaboratoryExceptions.SessionNotFoundInLaboratoryServerException:
                # We did not find the specified session in the laboratory server.
                # We'll finish the experiment.
                #self._update_command(command_id_pack, Command.Command("ERROR: SessionNotFound: None"))
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass
                raise UserProcessingExceptions.NoCurrentReservationException(
                    'Experiment reservation expired'
                )
            except LaboratoryExceptions.FailedToSendCommandException, ftspe:
                # There was an error while trying to send the command. 
                # We'll finish the experiment.
                #self._update_command(command_id_pack, Command.Command("ERROR: " + str(ftspe)))
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass

                raise UserProcessingExceptions.FailedToSendCommandException(
                        "Failed to send command: %s" % ftspe
                    )
        else:
            pass # TODO


    def send_async_command(self, command):
        """
        Runs a command asynchronously. Status of the request may be checked
        through the check_async_command_status method.
        
        @param command The command to run
        @see check_async_command_status
        """
        if self._session.has_key('lab_session_id') and self._session.has_key('lab_coordaddr'):
            laboratory_server = self._locator.get_server_from_coordaddr(
                    self._session['lab_coordaddr'],
                    ServerType.Laboratory
                )
            command_id_pack = self._append_command(command)
            
            try:
                
                # We forward the request to the laboratory server, which
                # will forward it to the actual experiment. Because this is 
                # an asynchronous call, we will not receive the actual response
                # to the command, but simply an ID identifying our request. This also 
                # means that by the time this call returns, the real response to the
                # command is most likely not available yet.
                request_id = laboratory_server.send_async_command(
                        self._session['lab_session_id'],
                        command
                    )
                
                # If this was a standard, synchronous send_command, we would now store the response
                # we received, so that later, when the experiment finishes, the log is properly
                # written. However, the real response is not available yet, so we can't do that here.
                # Instead, we will store a reference to our usage object, so that we can later update it
                # when the response to the asynchronous command is ready.
                self._session["async_commands_ids"][request_id] = command_id_pack

                return request_id
            
            except LaboratoryExceptions.SessionNotFoundInLaboratoryServerException:
                self._update_command(command_id_pack, Command.Command("ERROR: SessionNotFound: None"))
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass
                raise UserProcessingExceptions.NoCurrentReservationException(
                    'Experiment reservation expired'
                )
            except LaboratoryExceptions.FailedToSendCommandException, ftspe:
                self._update_command(command_id_pack, Command.Command("ERROR: " + str(ftspe)))
                try:
                    self.finished_experiment()
                except UserProcessingExceptions.FailedToFreeReservationException:
                    pass

                raise UserProcessingExceptions.FailedToSendCommandException(
                        "Failed to send command: %s" % ftspe
                    )
        else:
            pass # TODO


    def update_latest_timestamp(self):
        self._session['latest_timestamp'] = self._utc_timestamp()

    def _append_command(self, command): 
        """
        _append_command(command)
        Appends a command to the internal list that registers every command that was sent 
        during a session.
        
        @param command The command that was sent
        @return A two-items tuple. First element is the index of the command in the internal list. 
        Second element is the command (as a Usage.CommandSent object) that we stored. This tuple is 
        needed to later update the register with the command's result.
        @see _update_command
        """
        timestamp_before = self._utc_timestamp()
        command_sent = Usage.CommandSent(command, timestamp_before)
        return self._session['experiment_usage'].append_command(command_sent), command_sent

    def _update_command(self, (command_id, command_sent), response):
        """
        _update_command((command_id, command_sent), response)
        Updates the response of a registererd command once the response to it is
        available.
        
        @param command_id First element of the tuple that identifies the command to update
        @param command_sent Second element of the tuple that identifiers the command to update
        @param response Response to register
        """
        timestamp_after = self._utc_timestamp()
        command_sent.response        = response
        command_sent.timestamp_after = timestamp_after
        self._session['experiment_usage'].update_command(command_id, command_sent)

    def _utc_timestamp(self):
        return self.time_module.time()

    def _store_file(self, file_content, file_info):
        # TODO: this is a very dirty way to implement this. Anyway until the good approach is taken, this will store the students programs
        # TODO: there should be two global variables: first, if store_student_files is not activated, do nothing.
        #       but, if store_student_files is activated, it should check that for a given experiment, they should be stored or not.
        #       For instance, I may want to store GPIB experiments but not FPGA experiments
        should_i_store = self._cfg_manager.get_value('core_store_students_programs',False)
        timestamp_before   = self._utc_timestamp()
        if should_i_store:
            # TODO not tested
            def get_time_in_str():
                cur_time = time_module.time()
                s = time_module.strftime('%Y_%m_%d___%H_%M_%S_',time_module.gmtime(cur_time))
                millis = int((cur_time - int(cur_time)) * 1000)
                return s + str(millis)

            if isinstance(file_content, unicode):
                file_content_encoded = file_content.encode('utf8')
            else:
                file_content_encoded = file_content
            deserialized_file_content = ExperimentUtil.deserialize(file_content_encoded)
            storage_path = self._cfg_manager.get_value('core_store_students_programs_path')
            user_information = self.get_user_information()
            relative_file_path = get_time_in_str() + '_' + user_information.login + '_' + self._session['session_id'].id
            sha_obj            = hashlib.new('sha')
            sha_obj.update(deserialized_file_content)
            file_hash          = sha_obj.hexdigest()

            where = storage_path + '/' + relative_file_path
            f = open(where,'w')
            f.write(deserialized_file_content)
            f.close()

            return Usage.FileSent(relative_file_path, "{sha}%s" % file_hash, timestamp_before, file_info = file_info)
        else:
            return Usage.FileSent("<file not stored>","<file not stored>", timestamp_before, file_info = file_info)

    # 
    # WLC
    # 
    def _finish_reservation(self):
        if self._session.has_key('reservation_id'):
            try:
                reservation_id = self._session.pop('reservation_id')
                self._coordinator.finish_reservation(reservation_id)
            except Exception, e:
                log.log( UserProcessor, log.LogLevel.Error, "Exception finishing reservation: %s" % e )
                log.log_exc( UserProcessor, log.LogLevel.Warning )
                return e

    #
    # Polling
    # 

    def _initialize_polling(self):
        self._session['session_polling'] = (
                self.time_module.time(),
                UserProcessor.EXPIRATION_TIME_NOT_SET
            )

    def poll(self):
        if self.is_polling():
            latest_poll, expiration_time = self._session['session_polling']
            self._session['session_polling'] = (
                    self.time_module.time(),
                    UserProcessor.EXPIRATION_TIME_NOT_SET
                )

    def _stop_polling(self):
        if self.is_polling():
            self._session.pop('session_polling')

    def _renew_expiration_time(self, expiration_time):
        if self.is_polling():
            self._session['session_polling'] = (
                    self.time_module.time(),
                    expiration_time
                )

    def is_expired(self):
        if not self.is_polling():
            return True

        current_time = self.time_module.time()
        latest_poll, expiration_time = self._session['session_polling']
        if current_time - latest_poll > self._cfg_manager.get_value(EXPERIMENT_POLL_TIME, DEFAULT_EXPERIMENT_POLL_TIME):
            return True
        elif expiration_time != UserProcessor.EXPIRATION_TIME_NOT_SET and current_time > expiration_time:
            return True
        return False

    def is_polling(self):
        return self._session.has_key('session_polling')
    
    #
    # admin service
    #
    
    def get_users(self):
        db_session_id        = self._session['db_session_id']
        return get_users(self._db_manager, db_session_id)

    def get_groups(self, parent_id=None):
        db_session_id         = self._session['db_session_id']
        return get_groups(self._db_manager, db_session_id, parent_id)
    
    def get_roles(self):
        db_session_id         = self._session['db_session_id']
        return get_roles(self._db_manager, db_session_id)

    def get_experiments(self):
        db_session_id         = self._session['db_session_id']
        return get_experiments(self._db_manager, db_session_id)

    def get_experiment_uses(self, from_date, to_date, group_id, experiment_id, start_row, end_row, sort_by):
        db_session_id         = self._session['db_session_id']
        return get_experiment_uses(self._db_manager, db_session_id, from_date, to_date, group_id, experiment_id, start_row, end_row, sort_by)

    def get_user_permissions(self):
        db_session_id         = self._session['db_session_id']
        return get_user_permissions(self._db_manager, db_session_id)

