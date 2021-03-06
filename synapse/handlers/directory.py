# -*- coding: utf-8 -*-
# Copyright 2014, 2015 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from twisted.internet import defer
from ._base import BaseHandler

from synapse.api.errors import SynapseError, Codes, CodeMessageException
from synapse.api.constants import EventTypes
from synapse.types import RoomAlias

import logging

logger = logging.getLogger(__name__)


class DirectoryHandler(BaseHandler):

    def __init__(self, hs):
        super(DirectoryHandler, self).__init__(hs)

        self.federation = hs.get_replication_layer()
        self.federation.register_query_handler(
            "directory", self.on_directory_query
        )

    @defer.inlineCallbacks
    def create_association(self, user_id, room_alias, room_id, servers=None):

        # TODO(erikj): Do auth.

        if not self.hs.is_mine(room_alias):
            raise SynapseError(400, "Room alias must be local")
            # TODO(erikj): Change this.

        # TODO(erikj): Add transactions.

        # TODO(erikj): Check if there is a current association.

        if not servers:
            servers = yield self.store.get_joined_hosts_for_room(room_id)

        if not servers:
            raise SynapseError(400, "Failed to get server list")

        yield self.store.create_room_alias_association(
            room_alias,
            room_id,
            servers
        )

    @defer.inlineCallbacks
    def delete_association(self, user_id, room_alias):
        # TODO Check if server admin

        if not self.hs.is_mine(room_alias):
            raise SynapseError(400, "Room alias must be local")

        room_id = yield self.store.delete_room_alias(room_alias)

        if room_id:
            yield self._update_room_alias_events(user_id, room_id)

    @defer.inlineCallbacks
    def get_association(self, room_alias):
        room_id = None
        if self.hs.is_mine(room_alias):
            result = yield self.store.get_association_from_room_alias(
                room_alias
            )

            if result:
                room_id = result.room_id
                servers = result.servers
        else:
            try:
                result = yield self.federation.make_query(
                    destination=room_alias.domain,
                    query_type="directory",
                    args={
                        "room_alias": room_alias.to_string(),
                    },
                    retry_on_dns_fail=False,
                )
            except CodeMessageException as e:
                logging.warn("Error retrieving alias")
                if e.code == 404:
                    result = None
                else:
                    raise

            if result and "room_id" in result and "servers" in result:
                room_id = result["room_id"]
                servers = result["servers"]

        if not room_id:
            raise SynapseError(
                404,
                "Room alias %r not found" % (room_alias.to_string(),),
                Codes.NOT_FOUND
            )

        extra_servers = yield self.store.get_joined_hosts_for_room(room_id)
        servers = set(extra_servers) | set(servers)

        # If this server is in the list of servers, return it first.
        if self.server_name in servers:
            servers = (
                [self.server_name]
                + [s for s in servers if s != self.server_name]
            )
        else:
            servers = list(servers)

        defer.returnValue({
            "room_id": room_id,
            "servers": servers,
        })
        return

    @defer.inlineCallbacks
    def on_directory_query(self, args):
        room_alias = RoomAlias.from_string(args["room_alias"])
        if not self.hs.is_mine(room_alias):
            raise SynapseError(
                400, "Room Alias is not hosted on this Home Server"
            )

        result = yield self.store.get_association_from_room_alias(
            room_alias
        )

        if result is not None:
            defer.returnValue({
                "room_id": result.room_id,
                "servers": result.servers,
            })
        else:
            raise SynapseError(
                404,
                "Room alias %r not found" % (room_alias.to_string(),),
                Codes.NOT_FOUND
            )

    @defer.inlineCallbacks
    def send_room_alias_update_event(self, user_id, room_id):
        aliases = yield self.store.get_aliases_for_room(room_id)

        msg_handler = self.hs.get_handlers().message_handler
        yield msg_handler.create_and_send_event({
            "type": EventTypes.Aliases,
            "state_key": self.hs.hostname,
            "room_id": room_id,
            "sender": user_id,
            "content": {"aliases": aliases},
        }, ratelimit=False)
