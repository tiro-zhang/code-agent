"""成员直接通信；可靠保存、后端通知及合法历史消费相互分离。"""
from dataclasses import dataclass
import builtins
import hashlib
import inspect

from .models import Message, new_id, TeamValidationError, validate_id, PROTOCOL_FIELDS
from .store import TeamStoreError
from .tasks import TaskBoard


MAX_MESSAGES = 2048
BODY_LIMIT = 65536
SUMMARY_LIMIT = 256


class MessageError(TeamStoreError):
    """邮箱或协议拒绝。"""


@dataclass
class Delivery:
    message_id: str | None
    recipient_id: str
    saved: bool
    notified: bool = False
    error: str = ''

    def to_dict(self):
        return vars(self).copy()



class Mailbox:
    def __init__(self, store, name, notify=None):
        self.store, self.name, self.notify = store, name, notify

    def _actor(self, team, identity):
        if identity not in team.members or not team.members[identity].active:
            raise MessageError('发送者或接收者不属于当前团队')
        return team.members[identity]

    def _recipient(self, team, name_or_id):
        member = team.members.get(name_or_id)
        if member is None:
            member = next((item for item in team.members.values() if item.name == name_or_id), None)
        if member is None or not member.active or (member.state == 'stopped' and not member.resume_allowed):
            raise MessageError('目标不存在或已停止接收')
        return member.member_id

    def _load(self, identity, team):
        raw = self.store.read_json(self.name, f'inboxes/{identity}.json')
        if (type(raw) is not dict or set(raw) != {'version', 'team_id', 'member_id', 'messages'}
                or type(raw['version']) is not int or raw['version'] != 1 or raw['team_id'] != team.team_id
                or raw['member_id'] != identity or type(raw['messages']) is not list or len(raw['messages']) > MAX_MESSAGES):
            raise MessageError('邮箱版本、归属或容量无效')
        try:
            messages = [Message.from_dict(item) for item in raw['messages']]
            if len({item.message_id for item in messages}) != len(messages) or any(
                item.team_id != team.team_id or item.recipient_id != identity or item.sender_id not in team.members
                for item in messages):
                raise MessageError('消息身份或归属损坏')
            return messages
        except TeamValidationError as error:
            raise MessageError(f'邮箱损坏：{error}') from error

    def _save(self, identity, team, messages):
        self.store.write_json(self.name, f'inboxes/{identity}.json', {'version': 1, 'team_id': team.team_id,
                              'member_id': identity, 'messages': [message.to_dict() for message in messages]})

    def _protocol(self, team, sender_id, recipient_id, type, fields):
        if type not in PROTOCOL_FIELDS or set(fields) != PROTOCOL_FIELDS[type]:
            raise MessageError('协议类型或专用字段无效')
        sender, recipient = self._actor(team, sender_id), self._actor(team, recipient_id)
        if type in {'task_assignment', 'plan_decision', 'shutdown_request'} and sender_id != team.lead_id:
            raise MessageError('只有 Lead 可发送指派、计划决定及停止请求')
        if type in {'plan_request', 'idle', 'result_submitted', 'shutdown_ack'} and (
                sender_id == team.lead_id or recipient_id != team.lead_id):
            raise MessageError('成员事件必须由真实成员发给 Lead')
        for key, kind in {'goal_id': 'goal', 'task_id': 'task', 'claim_id': 'claim', 'run_id': 'run'}.items():
            if key in fields:
                try:
                    validate_id(fields[key], kind)
                except TeamValidationError as error:
                    raise MessageError(str(error)) from error
        if 'goal_id' in fields and (fields['goal_id'] not in team.goals or fields['goal_id'] != team.active_goal_id):
            raise MessageError('协议不是当前目标')
        if 'task_id' in fields:
            _, tasks = TaskBoard(self.store, self.name)._load()
            task = tasks.get(fields['task_id'])
            member = recipient if sender_id == team.lead_id else sender
            if not task or task.goal_id != fields['goal_id'] or task.owner_id != member.member_id or task.claim_id != fields['claim_id']:
                raise MessageError('协议任务、成员或领取关联不匹配')
        if type in {'plan_request', 'plan_decision'}:
            if (not isinstance(fields['plan_id'], str) or not fields['plan_id']
                    or builtins.type(fields['plan_version']) is not int or fields['plan_version'] < 1):
                raise MessageError('计划身份或版本无效')
            member = recipient if type == 'plan_decision' else sender
            if (member.task_id != fields['task_id'] or member.claim_id != fields['claim_id']
                    or member.plan_id != fields['plan_id'] or member.plan_version != fields['plan_version']):
                raise MessageError('计划不是当前领取版本')
            if type == 'plan_decision' and (builtins.type(fields['approved']) is not bool or member.state != 'awaiting_approval'):
                raise MessageError('计划当前不在审批等待或决定无效')
        if type in {'shutdown_request', 'shutdown_ack'}:
            member = recipient if type == 'shutdown_request' else sender
            if not isinstance(fields['generation'], str) or not fields['generation'] or member.generation != fields['generation']:
                raise MessageError('停止协议运行代次不匹配')
            if type == 'shutdown_ack' and (fields['stopped'] is not True or member.state != 'stopped'):
                raise MessageError('停止确认必须来自已确认真实收尾的成员')

    async def send(self, sender_id, recipient, body, *, type='text', fields=None, summary=None, call_id=None, broadcast_id=None):
        fields = {} if fields is None else fields
        if (not isinstance(body, str) or len(body.encode()) > BODY_LIMIT or builtins.type(fields) is not dict
                or (summary is not None and (not isinstance(summary, str) or len(summary) > SUMMARY_LIMIT))
                or (call_id is not None and (not isinstance(call_id, str) or not call_id or len(call_id) > 256))):
            raise MessageError('正文、摘要、调用身份或协议字段无效／超限')
        team = self.store.load(self.name)
        self._actor(team, sender_id)
        identity = None
        if call_id is None:
            async with self.store.lock(self.name, 'state.lock'):
                team = self.store.load(self.name)
                self._actor(team, sender_id)
                identity = self._recipient(team, recipient)
        message_id = new_id('message')
        if call_id is not None:
            digest = hashlib.sha256((sender_id + ':' + call_id).encode()).hexdigest()
            record_path = f'delivery-{digest}.json'
            async with self.store.lock(self.name, 'state.lock'):
                try:
                    record = self.store.read_json(self.name, record_path)
                except TeamStoreError as error:
                    if not isinstance(error.__cause__, FileNotFoundError):
                        raise
                    team = self.store.load(self.name)
                    self._actor(team, sender_id)
                    identity = self._recipient(team, recipient)
                    record = {'version': 1, 'sender_id': sender_id, 'call_id': call_id,
                              'requested_recipient': recipient, 'recipient_id': identity,
                              'message_id': message_id, 'body': body, 'type': type, 'fields': fields,
                              'summary': summary, 'broadcast_id': broadcast_id}
                    self.store.write_json(self.name, record_path, record)
                if (record.get('version') != 1 or record.get('sender_id') != sender_id
                        or record.get('call_id') != call_id or record.get('requested_recipient') != recipient
                        or record.get('body') != body or record.get('type') != type
                        or record.get('fields') != fields or record.get('summary') != summary
                        or record.get('broadcast_id') != broadcast_id):
                    raise MessageError('发送调用身份与内容冲突')
                identity, message_id = record['recipient_id'], record['message_id']
                validate_id(identity, 'member')
                validate_id(message_id, 'message')
        async with self.store.lock(self.name, f'locks/inbox-{identity}.lock'):
            team = self.store.load(self.name)
            self._actor(team, sender_id)
            # 初次解析后保持稳定身份；改名不会把本次消息路由给另一个成员。
            self._recipient(team, identity)
            messages = self._load(identity, team)
            existing = next((item for item in messages if call_id is not None and item.sender_id == sender_id and item.call_id == call_id), None)
            if existing:
                if (existing.body, existing.type, existing.fields, existing.broadcast_id) != (body, type, fields, broadcast_id):
                    raise MessageError('重复调用身份对应不同内容')
                message = existing
            else:
                self._protocol(team, sender_id, identity, type, fields)
                if len(messages) >= MAX_MESSAGES:
                    raise MessageError('邮箱已达到 2048 条容量上限')
                message = Message(message_id, team.team_id, sender_id, identity, body,
                                  summary if summary is not None else body[:SUMMARY_LIMIT], type=type,
                                  fields=fields, call_id=call_id, broadcast_id=broadcast_id)
                messages.append(message)
                self._save(identity, team, messages)
        delivery = Delivery(message.message_id, identity, True)
        team = self.store.load(self.name)
        if team.status == 'active' and self.notify is not None:
            try:
                result = self.notify(identity)
                if inspect.isawaitable(result):
                    await result
                delivery.notified = True
            except Exception as error:
                delivery.error = f'消息已保存，通知失败：{error}'
        return delivery

    async def read(self, member_id, *, unread=True):
        async with self.store.lock(self.name, f'locks/inbox-{member_id}.lock'):
            team = self.store.load(self.name)
            self._actor(team, member_id)
            messages = self._load(member_id, team)
            return [message for message in messages if not unread or not message.read]

    async def ack(self, member_id, ids):
        ids = set(ids)
        async with self.store.lock(self.name, f'locks/inbox-{member_id}.lock'):
            team = self.store.load(self.name)
            self._actor(team, member_id)
            messages = self._load(member_id, team)
            if ids - {message.message_id for message in messages}:
                raise MessageError('不能消费不属于此邮箱的消息')
            changed = False
            for message in messages:
                if message.message_id in ids and not message.read:
                    message.read, changed = True, True
            if changed:
                self._save(member_id, team, messages)

    async def reconcile(self, member_id, consumed_ids):
        messages = await self.read(member_id, unread=False)
        present = {message.message_id for message in messages}
        await self.ack(member_id, set(consumed_ids) & present)

    async def consume(self, member_id, commit, *, consumed_ids):
        """commit 必须一次持久提交合法历史及消息身份；取消前不消费。"""
        consumed = set(consumed_ids)
        await self.reconcile(member_id, consumed)
        messages = [message for message in await self.read(member_id) if message.message_id not in consumed]
        if not messages:
            return []
        result = commit(messages)
        if inspect.isawaitable(result):
            await result
        # 此步骤失败时合法历史是事实源，下次 reconcile 修复而不再次注入。
        await self.ack(member_id, [message.message_id for message in messages])
        return messages

    async def broadcast(self, sender_id, body, *, broadcast_id, summary=None):
        if not isinstance(broadcast_id, str) or not broadcast_id or len(broadcast_id) > 256:
            raise MessageError('广播必须提供稳定身份')
        team = self.store.load(self.name)
        self._actor(team, sender_id)
        digest = hashlib.sha256((sender_id + ':' + broadcast_id).encode()).hexdigest()
        record_path = f'broadcast-{digest}.json'
        async with self.store.lock(self.name, 'state.lock'):
            try:
                record = self.store.read_json(self.name, record_path)
            except TeamStoreError as error:
                if not isinstance(error.__cause__, FileNotFoundError):
                    raise
                record = {'version': 1, 'sender_id': sender_id, 'broadcast_id': broadcast_id,
                          'body': body, 'summary': summary, 'deliveries': {},
                          'recipients': [member.member_id for member in team.members.values()
                              if member.active and member.state != 'stopped' and member.member_id != sender_id]}
                self.store.write_json(self.name, record_path, record)
            if (record.get('version') != 1 or record.get('sender_id') != sender_id
                    or record.get('broadcast_id') != broadcast_id or record.get('body') != body
                    or record.get('summary') != summary or not isinstance(record.get('recipients'), list)):
                raise MessageError('广播身份或内容冲突')
            snapshot = record['recipients']
        results = []
        for identity in snapshot:
            previous = record.get('deliveries', {}).get(identity)
            if previous and previous['saved'] and not previous['error']:
                results.append(Delivery(**previous))
                continue
            call_id = 'broadcast-' + hashlib.sha256((broadcast_id + ':' + identity).encode()).hexdigest()
            try:
                results.append(await self.send(sender_id, identity, body, summary=summary,
                                               call_id=call_id, broadcast_id=broadcast_id))
            except TeamStoreError as error:
                results.append(Delivery(None, identity, False, error=str(error)))
            async with self.store.lock(self.name, 'state.lock'):
                current = self.store.read_json(self.name, record_path)
                current.setdefault('deliveries', {})[identity] = results[-1].to_dict()
                self.store.write_json(self.name, record_path, current)
                record = current
        return results
