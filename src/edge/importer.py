from django.db import connection
from BCBio import GFF
from edge.models import *
import time


class GFFImporter(object):

    def __init__(self, genome, gff_fasta_fn):
        self.__genome = genome
        self.__gff_fasta_fn = gff_fasta_fn

    def do_import(self):
        in_file = self.__gff_fasta_fn
        in_handle = open(in_file)

        # In DEBUG=True mode, Django keeps list of queries and blows up memory
        # usage when doing a big import. The following line disables this
        # logging.
        connection.use_debug_cursor = False

        for rec in GFF.parse(in_handle):
            f = GFFFragmentImporter(rec).do_import()
            self.__genome.genome_fragment_set.create(fragment=f, inherited=False)

        # Be nice and turn debug cursor back on
        connection.use_debug_cursor = True
        in_handle.close()


class GFFFragmentImporter(object):
    def __init__(self, gff_rec):
        self.__rec = gff_rec
        self.__sequence = None
        self.__features = None
        self.__fclocs = None

    def do_import(self):
        self.parse_gff()
        t0 = time.time()
        f = self.build_fragment()
        print 'build fragment: %.4f' % (time.time()-t0,)
        t0 = time.time()
        self.annotate(f)
        print 'annotate: %.4f' % (time.time()-t0,)
        return f

    def parse_gff(self):
        name_fields = ('name', 'Name', 'gene', 'locus', 'locus_tag', 'product', 'protein_id')

        self.__sequence = str(self.__rec.seq)
        seqlen = len(self.__sequence)
        print '%s: %s' % (self.__rec.id, seqlen)

        features = []
        for feature in self.__rec.features:
            # skip features that cover the entire sequence
            if feature.location.start == 0 and feature.location.end == seqlen:
                continue

            # get name
            name = feature.id
            if name == '':
                name = feature.type
            for field in name_fields:
                if field in feature.qualifiers:
                    v = feature.qualifiers[field]
                    if len(v) > 0:
                        name = v[0]
                        break
            name = name[0:100]

            # get qualifiers
            qualifiers = {}
            for field in feature.qualifiers:
                v = feature.qualifiers[field]
                if len(v) > 0:
                    qualifiers[field] = v

            # start in Genbank format is start after, so +1 here
            features.append((feature.location.start+1, feature.location.end,
                             name, feature.type, feature.strand, qualifiers))
        self.__features = features

    def build_fragment(self):
        # pre-chunk the fragment sequence at feature start and end locations.
        # there should be no need to further divide any chunk during import.
        break_points = list(set([f[0] for f in self.__features]+[f[1]+1 for f in self.__features]))
        break_points = sorted(break_points)
        chunk_sizes = []
        for i, bp in enumerate(break_points):
            if i == 0:
                if bp > 1:
                    chunk_sizes.append(break_points[i]-1)
            else:
                chunk_sizes.append(break_points[i]-break_points[i-1])
        print '%d chunks' % (len(chunk_sizes),)

        new_fragment = Fragment(name=self.__rec.id, circular=False, parent=None, start_chunk=None)
        new_fragment.save()
        new_fragment = new_fragment.indexed_fragment()

        prev = None
        flen = 0
        seqlen = len(self.__sequence)
        for sz in chunk_sizes:
            prev = new_fragment._append_to_fragment(prev, flen, self.__sequence[flen:flen+sz])
            flen += sz
        if flen < seqlen:
            f = new_fragment._append_to_fragment(prev, flen, self.__sequence[flen:seqlen])

        return new_fragment

    def annotate(self, fragment):
        self.__fclocs = {c.base_first: c
                         for c in Fragment_Chunk_Location.objects
                                                         .select_related('chunk')
                                                         .filter(fragment=fragment)}

        for feature in self.__features:
            f_start, f_end, f_name, f_type, f_strand, f_qualifiers = feature
            # print '  %s %s: %s-%s %s' % (f_type, f_name, f_start, f_end, f_strand)
            self._annotate_feature(fragment, f_start, f_end, f_name, f_type, f_strand, f_qualifiers)

    def _annotate_feature(self, fragment, first_base1, last_base1, name, type, strand, qualifiers):
        if fragment.circular and last_base1 < first_base1:
            # has to figure out the total length from last chunk
            length = len(self.__sequence)-first_base1+1+last_base1
        else:
            length = last_base1-first_base1+1
            if length <= 0:
                raise Exception('Annotation must have length one or more')

        if first_base1 not in self.__fclocs or\
           (last_base1 < len(self.__sequence) and last_base1+1 not in self.__fclocs):
            raise Exception('Missing chunks for feature')

        annotation_start = self.__fclocs[first_base1]
        if last_base1 != len(self.__sequence):
            annotation_end = self.__fclocs[last_base1+1]
        else:
            annotation_end = self.__fclocs[1]

        new_feature = fragment._add_feature(name, type, length, strand, qualifiers)

        fc = annotation_start
        a_i = 1
        while True:
            chunk = fc.chunk
            fragment._annotate_chunk(chunk, new_feature, a_i, a_i+len(chunk.sequence)-1)
            a_i += len(chunk.sequence)
            if fc.base_last+1 in self.__fclocs:
                fc = self.__fclocs[fc.base_last+1]
            else:
                fc = self.__fclocs[1]
            if fc.id == annotation_end.id:
                break
