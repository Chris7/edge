from sqlalchemy.sql import select, and_, func
from edge.models import fragment_table, chunk_table, annotation_table, chunk_annotation_table
from edge.models import fragment_chunk_location_table, edge_table
from edge.models import Annotation, Edge


class Fragment_Chunk(object):

    def __init__(self, chunk_id, fragment):
        self.__id = chunk_id
        self.__fragment = fragment
        self.reload()

    def reload(self):
        stmt = \
            select([chunk_table.c.sequence, chunk_table.c.initial_fragment_id])\
            .where(chunk_table.c.id == self.id)
        res = self.fragment.conn.execute(stmt)
        r = res.fetchone()
        res.close()
        self.__sequence = r[0]
        self.__initial_fragment_id = r[1]
        self.__out_edges = None
        self.__location = None
        self.__prev_chunk = None

    def __str__(self):
        return '%s: %s-%s' % (self.fragment.name, self.location[0], self.location[1])

    @property
    def id(self):
        return self.__id

    @property
    def fragment(self):
        return self.__fragment

    @property
    def sequence(self):
        return self.__sequence

    @property
    def initial_fragment_id(self):
        return self.__initial_fragment_id

    @property
    def out_edges(self):
        if self.__out_edges:
            return self.__out_edges

        edges = []
        stmt = \
            select([edge_table.c.from_chunk_id,
                    edge_table.c.fragment_id,
                    edge_table.c.to_chunk_id])\
            .where(edge_table.c.from_chunk_id == self.id)
        res = self.fragment.conn.execute(stmt)
        for r in res:
            edges.append(Edge(**r))

        self.__out_edges = edges
        return edges

    @property
    def next_chunk_id(self):
        # check inheritance hierarchy to figure out which edge to use.
        if len(self.out_edges) == 0:
            return None
        else:
            # sort edges by predecessor level if more than one edge
            if len(self.out_edges) > 1:
                pp = self.fragment.predecessor_priorities()

                def sorter_f(e):
                    return pp[e.fragment_id] if e.fragment_id in pp else len(pp)
                out_edges = sorted(self.out_edges, key=sorter_f)
            else:
                out_edges = self.out_edges
            return out_edges[0].to_chunk_id

    @property
    def next_chunk(self):
        if self.next_chunk_id:
            return Fragment_Chunk(self.next_chunk_id, self.fragment)
        return None

    @property
    def location(self):
        if self.__location is not None:
            return self.__location

        stmt = \
            select([fragment_chunk_location_table.c.base_first,
                    fragment_chunk_location_table.c.base_last])\
            .where(and_(fragment_chunk_location_table.c.fragment_id == self.fragment.id,
                        fragment_chunk_location_table.c.chunk_id == self.id))

        res = self.fragment.conn.execute(stmt)
        location = res.fetchone()
        res.close()

        self.__location = location
        return location

    @property
    def prev_chunk(self):
        if self.__prev_chunk is not None:
            return self.__prev_chunk

        loc = self.location
        if loc[0] == 1:
            return None

        stmt = \
            select([fragment_chunk_location_table.c.chunk_id])\
            .where(and_(fragment_chunk_location_table.c.fragment_id == self.fragment.id,
                        fragment_chunk_location_table.c.base_last == loc[0]-1))

        res = self.fragment.conn.execute(stmt)
        prev_chunk_id = res.fetchone()[0]
        res.close()

        self.__prev_chunk = Fragment_Chunk(prev_chunk_id, self.fragment)
        return self.__prev_chunk

    def annotations(self):
        stmt = \
            select([annotation_table.c.id,
                    annotation_table.c.name,
                    annotation_table.c.type,
                    annotation_table.c.strand,
                    annotation_table.c.length,
                    chunk_annotation_table.c.annotation_base_first,
                    chunk_annotation_table.c.annotation_base_last])\
            .where(and_(chunk_annotation_table.c.chunk_id == self.id,
                        chunk_annotation_table.c.annotation_id == annotation_table.c.id))

        cur = self.fragment.conn.execute(stmt)
        annotations = []
        for f in cur:
            annotations.append(Annotation(first_bp=None, last_bp=None, annotation_id=f[0],
                                          name=f[1], type=f[2], strand=f[3],
                                          annotation_full_length=f[4],
                                          annotation_first_bp=f[5],
                                          annotation_last_bp=f[6]))
        return annotations


class FragmentNotFound(Exception):
    pass


class Fragment(object):

    def __init__(self, connector, id):
        self._connector = connector
        self._fragment_id = id
        self._fragment = self.__get_fragment(id)
        self._predecessors = None
        self._predecessor_priorities = None

    @property
    def id(self):
        return self._fragment_id

    @property
    def name(self):
        return self._fragment['name']

    @property
    def circular(self):
        return self._fragment['circular']

    @property
    def start_chunk_id(self):
        return self._fragment['start_chunk_id']

    def _set_start_chunk_id(self, chunk_id):
        self._fragment['start_chunk_id'] = chunk_id

    @property
    def conn(self):
        return self._connector.conn

    @property
    def length(self):
        stmt = \
            select([func.max(fragment_chunk_location_table.c.base_last)])\
            .where(fragment_chunk_location_table.c.fragment_id == self.id)
        total_bases = self.conn.execute(stmt).scalar()
        return total_bases

    @property
    def parent_id(self):
        return self._fragment['parent_id']

    def __get_fragment(self, id):
        stmt = select([fragment_table.c.name, fragment_table.c.circular, fragment_table.c.parent_id,
                       fragment_table.c.start_chunk_id]).where(fragment_table.c.id == id)
        cur = self.conn.execute(stmt)
        fragment = None
        for f in cur:
            fragment = {'name': f[0], 'circular': f[1], 'parent_id': f[2], 'start_chunk_id': f[3]}
        if fragment is None:
            raise FragmentNotFound('Cannot find fragment %s' % (id,))
        return fragment

    def __get_predecessors(self):
        predecessors = [self._fragment_id]
        f_id = self._fragment['parent_id']

        while f_id is not None:
            predecessors.append(f_id)
            parent = self.__get_fragment(f_id)
            f_id = parent['parent_id']

        self._predecessors = predecessors
        self._predecessor_priorities = {f: i for i, f in enumerate(predecessors)}

    def predecessors(self):
        if self._predecessors is None:
            self.__get_predecessors()
        return self._predecessors

    def predecessor_priorities(self):
        if self._predecessor_priorities is None:
            self.__get_predecessors()
        return self._predecessor_priorities

    def get_chunk(self, chunk_id):
        return Fragment_Chunk(chunk_id, self)

    def chunks(self):
        chunk_id = self.start_chunk_id

        while chunk_id is not None:
            chunk = self.get_chunk(chunk_id)
            yield chunk
            chunk_id = chunk.next_chunk_id

    def get_sequence(self, bp_lo=None, bp_hi=None):
        c = and_(fragment_chunk_location_table.c.fragment_id == self.id,
                 fragment_chunk_location_table.c.chunk_id == chunk_table.c.id)
        if bp_lo is not None:
            c = and_(c, fragment_chunk_location_table.c.base_last >= bp_lo)
        if bp_hi is not None:
            c = and_(c, fragment_chunk_location_table.c.base_first <= bp_hi)

        stmt = \
            select([chunk_table.c.sequence,
                    fragment_chunk_location_table.c.base_first,
                    fragment_chunk_location_table.c.base_last,
                    fragment_chunk_location_table.c.fragment_id,
                    fragment_chunk_location_table.c.chunk_id])\
            .where(c)\
            .order_by(fragment_chunk_location_table.c.base_first)

        sequence = []
        last_chunk_base_last = None

        cur = self.conn.execute(stmt)
        for row in cur:
            s, base_first, base_last, fragment_id, chunk_id = row
            if last_chunk_base_last is not None and base_first != last_chunk_base_last+1:
                raise Exception('Fragment chunk location table missing chunks before %s'
                                % (base_first,))
            if bp_lo is not None and base_first < bp_lo:
                s = s[bp_lo-base_first:]
            if bp_hi is not None and base_last > bp_hi:
                s = s[:bp_hi-base_last]
            sequence.append(s)
            last_chunk_base_last = base_last

        return ''.join(sequence)

    @property
    def sequence(self):
        return self.get_sequence()

    def annotations(self, bp_lo=None, bp_hi=None):
        c = and_(chunk_annotation_table.c.chunk_id == fragment_chunk_location_table.c.chunk_id,
                 chunk_annotation_table.c.annotation_id == annotation_table.c.id,
                 fragment_chunk_location_table.c.fragment_id == self.id)
        if bp_lo is not None:
            c = and_(c, fragment_chunk_location_table.c.base_last >= bp_lo)
        if bp_hi is not None:
            c = and_(c, fragment_chunk_location_table.c.base_first <= bp_hi)

        stmt = \
            select([annotation_table.c.id,
                    annotation_table.c.name,
                    annotation_table.c.type,
                    annotation_table.c.strand,
                    annotation_table.c.length,
                    chunk_annotation_table.c.annotation_base_first,
                    chunk_annotation_table.c.annotation_base_last,
                    fragment_chunk_location_table.c.base_first,
                    fragment_chunk_location_table.c.base_last])\
            .where(c)\
            .order_by(annotation_table.c.id,
                      fragment_chunk_location_table.c.base_first)

        cur = self.conn.execute(stmt)
        annotations = []
        for f in cur:
            if len(annotations) > 0 and\
               annotations[-1].annotation_id == f[0] and\
               annotations[-1].annotation_last_bp == f[5]-1 and\
               annotations[-1].last_bp == f[7]-1:
                # merge annotation
                annotations[-1].last_bp = f[8]
                annotations[-1].annotation_last_bp = f[6]
            else:
                annotations.append(Annotation(first_bp=f[7], last_bp=f[8],
                                              annotation_id=f[0],
                                              name=f[1], type=f[2], strand=f[3],
                                              annotation_full_length=f[4],
                                              annotation_first_bp=f[5],
                                              annotation_last_bp=f[6]))
        return annotations


class Fragment_Operator(Fragment):

    def __init__(self, *args, **kwargs):
        super(Fragment_Operator, self).__init__(*args, **kwargs)
        self.__updated = []

    def updated(self):
        return self.__updated

    def last_updated(self):
        if len(self.__updated) == 0:
            return None
        return self.__updated[-1]

    def _add_updated(self, operator):
        self.__updated.append(operator)

    def update(self, name):
        from edge.fragment_updater import Fragment_Updater

        new_connector = self._connector.new_connector(with_transaction=True)
        new_fragment_id = Fragment_Updater.add_fragment(
            new_connector.conn, name, self.circular, self._fragment_id, self.start_chunk_id
        )

        # copy over location index
        stmt = \
            select([fragment_chunk_location_table.c.chunk_id,
                    fragment_chunk_location_table.c.base_first,
                    fragment_chunk_location_table.c.base_last])\
            .where(fragment_chunk_location_table.c.fragment_id == self._fragment_id)

        res = new_connector.conn.execute(stmt)
        values = []
        for row in res:
            values.append(dict(fragment_id=new_fragment_id,
                               chunk_id=row[0],
                               base_first=row[1],
                               base_last=row[2]))
        stmt = fragment_chunk_location_table.insert()
        new_connector.conn.execute(stmt, values)

        return Fragment_Updater(self, True, new_connector, new_fragment_id)

    def annotate(self):
        from edge.fragment_updater import Fragment_Annotator

        new_connector = self._connector.new_connector(with_transaction=True)
        return Fragment_Annotator(None, new_connector, self.id)
