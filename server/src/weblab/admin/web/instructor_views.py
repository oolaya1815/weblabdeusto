from flask import redirect, request, flash
from flask.ext.admin import expose, AdminIndexView, BaseView
from flask.ext.admin.contrib.sqla import ModelView

import weblab.permissions as permissions
import weblab.db.model as model

def get_app_instance():
    import weblab.admin.web.app as admin_app
    return admin_app.AdministrationApplication.INSTANCE

def is_instructor():
    is_admin = get_app_instance().is_admin()
    if is_admin:
        return True
    
    return get_app_instance().get_user_role() in ('admin', 'instructor')

class InstructorView(BaseView):
    def is_accessible(self):
        return is_instructor()

    def _handle_view(self, name, **kwargs):
        if not self.is_accessible():
            return redirect(request.url.split('/weblab/instructor')[0] + '/weblab/client')

        return super(InstructorView, self)._handle_view(name, **kwargs)

class InstructorModelView(ModelView):
    def is_accessible(self):
        return is_instructor()

    def _handle_view(self, name, **kwargs):
        if not self.is_accessible():
            return redirect(request.url.split('/weblab/instructor')[0] + '/weblab/client')

        return super(InstructorModelView, self)._handle_view(name, **kwargs)

class InstructorHomeView(AdminIndexView):
    def __init__(self, db_session, **kwargs):
        self._db_session = db_session
        super(InstructorHomeView, self).__init__(**kwargs)

    @expose()
    def index(self):
        user_information = get_app_instance().get_user_information()
        return self.render("instructor-index.html", is_admin = get_app_instance().is_admin(), admin_url = get_app_instance().full_admin_url, user_information = user_information)

    def is_accessible(self):
        return is_instructor()

    def _handle_view(self, name, **kwargs):
        if not self.is_accessible():
            return redirect(request.url.split('/weblab/administration')[0] + '/weblab/client')

        return super(InstructorHomeView, self)._handle_view(name, **kwargs)

def get_assigned_group_ids(session):
    # If the user is an administrator, permissions are not relevant.
    if get_app_instance().is_admin():
        return [ group.id for group in session.query(model.DbGroup).all() ]

    # Otherwise, check the permissions and only return those groups
    # where the user has a specific permission
    group_ids = set()

    for permission in get_app_instance().get_permissions():
        if permission.name == permissions.INSTRUCTOR_OF_GROUP:
            group_id = permission.get_parameter_value(permissions.TARGET_GROUP)
            if group_id is not None:
                group_ids.add(int(group_id))

    return group_ids

def get_assigned_groups(session):
    group_ids = get_assigned_group_ids(session)

    return session.query(model.DbGroup).filter(model.DbGroup.id.in_(group_ids))

def apply_instrutor_filters_to_logs(session, logs_query):
    """ logs_query is a sqlalchemy query. Here we filter that 
    the teacher only sees those permissions for a group.
    """
    group_ids = get_assigned_group_ids(session)

    permission_ids = set()

    for permission in session.query(model.DbGroupPermission).filter(model.DbGroupPermission.group_id.in_(group_ids), model.DbGroupPermission.permission_type == permissions.EXPERIMENT_ALLOWED).all():
        permission_ids.add(permission.id)

    logs_query.filter(model.DbUserUsedExperiment.group_permission_id.in_(permission_ids))
    return logs_query


class UsersPanel(InstructorModelView):

    can_edit = can_delete = can_create = False

    def __init__(self, session, **kwargs):
        super(UsersPanel, self).__init__(model.DbUser, session, **kwargs)

    def get_query(self):
        query = super(UsersPanel, self).get_query()
        groups = get_assigned_groups(self.session).subquery()
        query = query.join(groups, model.DbUser.groups)
        return query

    def get_count_query(self):
        query = super(UsersPanel, self).get_count_query()
        groups = get_assigned_groups(self.session).subquery()
        query = query.join(groups, model.DbUser.groups)
        return query

class GroupsPanel(InstructorModelView):

    can_edit = can_delete = can_create = False

    def __init__(self, session, **kwargs):
        super(GroupsPanel, self).__init__(model.DbGroup, session, **kwargs)

    def get_query(self):
        query = super(GroupsPanel, self).get_query()
        query = query.filter(model.DbGroup.id.in_(get_assigned_group_ids(self.session)))
        return query

    def get_count_query(self):
        query = super(GroupsPanel, self).get_count_query()
        query = query.filter(model.DbGroup.id.in_(get_assigned_group_ids(self.session)))
        return query


class UserUsedExperimentPanel(InstructorModelView):

    can_edit = can_delete = can_create = False

    list_columns = ['user', 'experiment', 'start_date', 'end_date', 'origin']

    def __init__(self, session, **kwargs):
        super(UserUsedExperimentPanel, self).__init__(model.DbUserUsedExperiment, session, **kwargs)

    def get_query(self):
        query = super(UserUsedExperimentPanel, self).get_query()
        query = apply_instrutor_filters_to_logs(self.session, query)
        return query


    def get_count_query(self):
        query = super(UserUsedExperimentPanel, self).get_count_query()
        query = apply_instrutor_filters_to_logs(self.session, query)
        return query