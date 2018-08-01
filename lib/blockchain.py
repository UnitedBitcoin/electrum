# Electrum - lightweight Bitcoin client
# Copyright (C) 2012 thomasv@ecdsa.org
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import os
import threading

from . import util
from . import bitcoin
from .bitcoin import *

MAX_TARGET = 0x00000000FFFF0000000000000000000000000000000000000000000000000000

def serialize_header(res):
    s = int_to_hex(res.get('version'), 4) \
        + rev_hex(res.get('prev_block_hash')) \
        + rev_hex(res.get('merkle_root')) \
        + int_to_hex(int(res.get('timestamp')), 4) \
        + int_to_hex(int(res.get('bits')), 4) \
        + int_to_hex(int(res.get('nonce')), 4)
    return s

def deserialize_header(s, height):
    hex_to_int = lambda s: int('0x' + bh2u(s[::-1]), 16)
    h = {}
    h['version'] = hex_to_int(s[0:4])
    h['prev_block_hash'] = hash_encode(s[4:36])
    h['merkle_root'] = hash_encode(s[36:68])
    h['timestamp'] = hex_to_int(s[68:72])
    h['bits'] = hex_to_int(s[72:76])
    h['nonce'] = hex_to_int(s[76:80])
    h['block_height'] = height
    return h

def hash_header(header):
    if header is None:
        return '0' * 64
    if header.get('prev_block_hash') is None:
        header['prev_block_hash'] = '00'*32
    return hash_encode(Hash(bfh(serialize_header(header))))


blockchains = {}

def read_blockchains(config):
    blockchains[0] = Blockchain(config, 0, None)
    fdir = os.path.join(util.get_headers_dir(config), 'forks')
    if not os.path.exists(fdir):
        os.mkdir(fdir)
    l = filter(lambda x: x.startswith('fork_'), os.listdir(fdir))
    l = sorted(l, key = lambda x: int(x.split('_')[1]))
    for filename in l:
        checkpoint = int(filename.split('_')[2])
        parent_id = int(filename.split('_')[1])
        b = Blockchain(config, checkpoint, parent_id)
        blockchains[b.checkpoint] = b
    return blockchains

def check_header(header):
    if type(header) is not dict:
        return False
    for b in blockchains.values():
        if b.check_header(header):
            return b
    return False

def can_connect(header):
    for b in blockchains.values():
        if b.can_connect(header):
            return b
    return False


class Blockchain(util.PrintError):
    """
    Manages blockchain headers and their verification
    """

    def __init__(self, config, checkpoint, parent_id):
        self.config = config
        self.catch_up = None # interface catching up
        self.checkpoint = checkpoint
        self.checkpoints = bitcoin.NetworkConstants.CHECKPOINTS
        self.parent_id = parent_id
        self.lock = threading.Lock()
        with self.lock:
            self.update_size()

    def parent(self):
        return blockchains[self.parent_id]

    def get_max_child(self):
        children = list(filter(lambda y: y.parent_id==self.checkpoint, blockchains.values()))
        return max([x.checkpoint for x in children]) if children else None

    def get_checkpoint(self):
        mc = self.get_max_child()
        return mc if mc is not None else self.checkpoint

    def get_branch_size(self):
        return self.height() - self.get_checkpoint() + 1

    def get_name(self):
        return self.get_hash(self.get_checkpoint()).lstrip('00')[0:10]

    def check_header(self, header):
        header_hash = hash_header(header)
        height = header.get('block_height')
        return header_hash == self.get_hash(height)

    def fork(parent, header):
        checkpoint = header.get('block_height')
        self = Blockchain(parent.config, checkpoint, parent.checkpoint)
        open(self.path(), 'w+').close()
        self.save_header(header)
        return self

    def height(self):
        return self.checkpoint + self.size() - 1

    def size(self):
        with self.lock:
            return self._size

    def update_size(self):
        p = self.path()
        self._size = os.path.getsize(p)//80 if os.path.exists(p) else 0

    def verify_header(self, header, prev_hash, target):
        _hash = hash_header(header)
        if prev_hash != header.get('prev_block_hash'):
            raise BaseException("prev hash mismatch: %s vs %s" % (prev_hash, header.get('prev_block_hash')))
        if bitcoin.NetworkConstants.TESTNET:
            return
        bits = self.target_to_bits(target)
        if bits != header.get('bits'):
            raise BaseException("bits mismatch: %s vs %s" % (bits, header.get('bits')))
        if util.IsProofOfStake(header["version"]):
            # ToDo: must check by stake value.
            pass

        elif int('0x' + _hash, 16) > target:
            raise BaseException("insufficient proof of work: %s vs target %s" % (int('0x' + _hash, 16), target))

    def verify_chunk(self, index, data):

        num = len(data) // 80
        if index == util.ForkData.pre_fork_max_index:
            num = util.ForkData.superblockendnumber - util.ForkData.pre_fork_max_index*2016
        prev_hash = self.get_hash(util.ub_start_height_of_index(index) - 1)
        if index >=util.ForkData.third_fork_max_index:
            raw_header = data[0:80]
            header = deserialize_header(raw_header, util.ub_start_height_of_index(index))
            target = self.get_target(index -1,util.IsProofOfStake(header["version"]))
            self.verify_header(header, prev_hash, target)
            #prev_hash = hash_header(header)
        else:
            target = self.get_target(index-1)
            for i in range(num):
                raw_header = data[i*80:(i+1) * 80]
                header = deserialize_header(raw_header, util.ub_start_height_of_index(index) + i)
                if not (util.ub_start_height_of_index(index) + i >=util.ForkData.superblockstartnumber and util.ub_start_height_of_index(index) + i<=util.ForkData.superblockendnumber):
                    self.verify_header(header, prev_hash, target)
                prev_hash = hash_header(header)

    def path(self):
        d = util.get_headers_dir(self.config)
        filename = 'blockchain_headers' if self.parent_id is None else os.path.join('forks', 'fork_%d_%d'%(self.parent_id, self.checkpoint))
        return os.path.join(d, filename)

    def save_chunk(self, index, chunk):
        filename = self.path()
        d = (util.ub_start_height_of_index(index) - self.checkpoint) * 80
        if d < 0:
            chunk = chunk[-d:]
            d = 0
        self.write(chunk, d)
        self.swap_with_parent()

    def swap_with_parent(self):
        if self.parent_id is None:
            return
        parent_branch_size = self.parent().height() - self.checkpoint + 1
        if parent_branch_size >= self.size():
            return
        self.print_error("swap", self.checkpoint, self.parent_id)
        parent_id = self.parent_id
        checkpoint = self.checkpoint
        parent = self.parent()
        with open(self.path(), 'rb') as f:
            my_data = f.read()
        with open(parent.path(), 'rb') as f:
            f.seek((checkpoint - parent.checkpoint)*80)
            parent_data = f.read(parent_branch_size*80)
        self.write(parent_data, 0)
        parent.write(my_data, (checkpoint - parent.checkpoint)*80)
        # store file path
        for b in blockchains.values():
            b.old_path = b.path()
        # swap parameters
        self.parent_id = parent.parent_id; parent.parent_id = parent_id
        self.checkpoint = parent.checkpoint; parent.checkpoint = checkpoint
        self._size = parent._size; parent._size = parent_branch_size
        # move files
        for b in blockchains.values():
            if b in [self, parent]: continue
            if b.old_path != b.path():
                self.print_error("renaming", b.old_path, b.path())
                os.rename(b.old_path, b.path())
        # update pointers
        blockchains[self.checkpoint] = self
        blockchains[parent.checkpoint] = parent

    def write(self, data, offset):
        filename = self.path()
        with self.lock:
            with open(filename, 'rb+') as f:
                if offset != self._size*80:
                    f.seek(offset)
                    f.truncate()
                f.seek(offset)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self.update_size()

    def save_header(self, header):
        delta = header.get('block_height') - self.checkpoint
        data = bfh(serialize_header(header))
        assert delta == self.size()
        assert len(data) == 80
        self.write(data, delta*80)
        self.swap_with_parent()

    def read_header(self, height):
        assert self.parent_id != self.checkpoint
        if height < 0:
            return
        if height < self.checkpoint:
            return self.parent().read_header(height)
        if height > self.height():
            return
        delta = height - self.checkpoint
        name = self.path()
        if os.path.exists(name):
            with open(name, 'rb') as f:
                f.seek(delta * 80)
                h = f.read(80)
        if h == bytes([0])*80:
            return None
        return deserialize_header(h, height)

    def get_hash(self, height):
        if height == -1:
            return '0000000000000000000000000000000000000000000000000000000000000000'
        elif height == 0:
            return bitcoin.NetworkConstants.GENESIS
        elif len(self.checkpoints)<util.ForkData.pre_fork_max_index :
            if height < len(self.checkpoints) * 2016:
                assert (height+1) % 2016 == 0
                index = height // 2016
                h, t = self.checkpoints[index]
                return h
            else:
                return hash_header(self.read_header(height))
        elif len(self.checkpoints)>=util.ForkData.pre_fork_max_index and len(self.checkpoints <util.ForkData.second_fork_max_index):
            if height < util.ForkData.pre_fork_max_index*2016:
                assert (height+1) % 2016 == 0
                index = height // 2016
                h, t = self.checkpoints[index]
                return h
            elif height < 499200+(len(self.checkpoints) -247 )* 200:
                assert (height -499200 + 1) % 200 == 0
                index = 247 + ((height-499200) // 200)
                h, t = self.checkpoints[index]
                return h
            else:
                return hash_header(self.read_header(height))
        elif len(self.checkpoints)>=util.ForkData.second_fork_max_index:
            if height < util.ForkData.pre_fork_max_index*2016:
                assert (height+1) % 2016 == 0
                index = height // 2016
                h, t = self.checkpoints[index]
                return h
            elif height < util.ForkData.fork_height+(len(self.checkpoints) -util.ForkData.pre_fork_max_index )* util.ForkData.after_chunk_size:
                assert (height -util.ForkData.fork_height + 1) % util.ForkData.after_chunk_size == 0
                index = util.ForkData.pre_fork_max_index + ((height-util.ForkData.fork_height) // util.ForkData.after_chunk_size)
                h, t = self.checkpoints[index]
                return h
            elif height< util.ForkData.second_fork_height + (len(self.checkpoints)-util.ForkData.second_fork_max_index)*util.ForkData.second_fork_chunk_size:
                assert (height - util.ForkData.second_fork_height + 1) % util.ForkData.second_fork_chunk_size == 0
                index = util.ForkData.second_fork_max_index + ((height - util.ForkData.second_fork_height) // util.ForkData.second_fork_chunk_size)
                h, t = self.checkpoints[index]
                return h
            else:
                return hash_header(self.read_header(height))


    def get_last_block_header(self,height,is_pos):
        start_header = self.read_header(height)
        while(height > util.ForkData.third_fork_height-util.ForkData.second_fork_chunk_size):
            if util.IsProofOfStake(start_header["version"]) == is_pos:
                return start_header,height
            height -=1
            start_header = self.read_header(height)
        return None,height


    def get_next_target_require(self,height,is_pos):
        first,height = self.get_last_block_header(height,is_pos)
        if first == None:
            return util.ub_default_diffculty(is_pos)
        nTargetTimespan = 2*10*60
        print("first",height)
        tNbits = []
        tempBits = set()
        tNbits.append(first)
        tempBits.add(first["bits"])
        for i in range(9):
            last_header,height = self.get_last_block_header(height-1,is_pos)
            tNbits.append(last_header)
            tempBits.add(last_header["bits"])
            if is_pos and height <= util.ForkData.third_fork_height:
                return util.ub_default_diffculty(is_pos)
            if last_header is None :
                if is_pos:
                    return util.ub_default_diffculty(is_pos)
                else:
                    return self.get_third_fork_pow_difficult()
            if len(tempBits)>1:
                return  self.bits_to_target(first["bits"])

        print(tNbits)
        print(tempBits)
        nLastPowTime = last_header["timestamp"]
        nFirstBlockTime = first["timestamp"]
        nActualTimespan = nFirstBlockTime - nLastPowTime
        nActualTimespan = max(nActualTimespan, nTargetTimespan // 4)
        nActualTimespan = min(nActualTimespan, nTargetTimespan * 4)
        target = self.bits_to_target(first["bits"])
        print("target:",hex(target))
        print("target bits", hex(self.target_to_bits(target)))
        if is_pos:
            new_target = min(util.ub_default_diffculty(is_pos), (target // nTargetTimespan)* nActualTimespan)
        else:
            new_target = min(util.ub_default_diffculty(is_pos), (target* nActualTimespan) // nTargetTimespan)
        print("target:", hex(new_target))
        print("target bits",hex(self.target_to_bits(new_target)))
        return new_target


    def get_third_fork_pow_difficult(self):
        data = self.read_header(util.ForkData.third_fork_height - 1)
        return self.bits_to_target(data["bits"])

    def get_target(self, index,is_pos = False):
        # compute target from chunk x, used in chunk x+1
        if bitcoin.NetworkConstants.TESTNET:
            return 0, 0
        if index == -1:
            return 0x1d00ffff, MAX_TARGET
        if index < len(self.checkpoints):
            h, t = self.checkpoints[index]
            return t
        old_check_index_count = util.ForkData.pre_fork_max_index
        first_block_num = util.ForkData.fork_height
        second_block_num = util.ForkData.second_fork_height
        second_check_index_count = util.ForkData.second_fork_max_index   # means first block 506200 last block 506399

        # new target
        if index <old_check_index_count:
            first = self.read_header(index * 2016)
            last = self.read_header(index * 2016 + 2015)
            bits = last.get('bits')
            target = self.bits_to_target(bits)
            nActualTimespan = last.get('timestamp') - first.get('timestamp')
            nTargetTimespan = 14 * 24 * 60 * 60
            nActualTimespan = max(nActualTimespan, nTargetTimespan // 4)
            nActualTimespan = min(nActualTimespan, nTargetTimespan * 4)
            new_target = min(MAX_TARGET, (target * nActualTimespan) // nTargetTimespan)
            return new_target
        elif index < second_check_index_count:
            print("block num",first_block_num+ (index-old_check_index_count) * 200)
            if first_block_num+ (index-old_check_index_count) * 200 < util.ForkData.superblockstartnumber:
                return self.bits_to_target(0x1800b0ed)
            elif first_block_num+ (index-old_check_index_count) * 200 <= util.ForkData.superblockendnumber:
                return self.bits_to_target(0x18451c94)
            if index == second_check_index_count-1:
                return self.bits_to_target(0x191a37d1)
            first = self.read_header(first_block_num+ (index-old_check_index_count) * 200)
            last = self.read_header(first_block_num+ (index-old_check_index_count) * 200 + 199)
            bits = last.get('bits')
            target = self.bits_to_target(bits)
            nActualTimespan = last.get('timestamp') - first.get('timestamp')
            nTargetTimespan = 200* 10 * 60
            nActualTimespan = max(nActualTimespan, nTargetTimespan // 4)
            nActualTimespan = min(nActualTimespan, nTargetTimespan * 4)
            new_target = min(MAX_TARGET, (target * nActualTimespan) // nTargetTimespan)
            return new_target
        elif index < util.ForkData.third_fork_max_index-1:
            print("second block num", second_block_num + (index - second_check_index_count) * 10)

            first = self.read_header(second_block_num + (index - second_check_index_count) * 10)
            last = self.read_header(second_block_num + (index - second_check_index_count) * 10 + 9)
            bits = last.get('bits')
            target = self.bits_to_target(bits)
            nActualTimespan = last.get('timestamp') - first.get('timestamp')
            nTargetTimespan = 10 * 1 * 60
            nActualTimespan = max(nActualTimespan, nTargetTimespan // 4)
            nActualTimespan = min(nActualTimespan, nTargetTimespan * 4)
            new_target = min(MAX_TARGET, (target * nActualTimespan) // nTargetTimespan)
            return new_target
        else:
            print("third fork block num ",util.ForkData.third_fork_height + index-util.ForkData.third_fork_max_index)
#修改为新的难度调整算法

            if is_pos and index - util.ForkData.third_fork_max_index <9:
                return util.ub_default_diffculty(is_pos,True)
            elif not is_pos and index - util.ForkData.third_fork_max_index <9:
                return self.get_third_fork_pow_difficult()

            return self.get_next_target_require(util.ub_start_height_of_index(index),is_pos)


    def bits_to_target(self, bits):
        bitsN = (bits >> 24) & 0xff
        #if not (bitsN >= 0x03 and bitsN <= 0x1d):
        #    raise BaseException("First part of bits should be in [0x03, 0x1d]")
        bitsBase = bits & 0xffffff
        if not (bitsBase >= 0x8000 and bitsBase <= 0x7fffff):
            raise BaseException("Second part of bits should be in [0x8000, 0x7fffff]")
        return bitsBase << (8 * (bitsN-3))

    def target_to_bits(self, target):
        c = ("%064x" % target)#[2:]
        while c[:2] == '00' and len(c) > 6:
            c = c[2:]
        bitsN, bitsBase = len(c) // 2, int('0x' + c[:6], 16)
        if bitsBase >= 0x800000:
            bitsN += 1
            bitsBase >>= 8
        return bitsN << 24 | bitsBase

    def can_connect(self, header, check_height=True):
        height = header['block_height']
        if check_height and self.height() != height - 1:
            #self.print_error("cannot connect at height", height)
            return False
        if height == 0:
            return hash_header(header) == bitcoin.NetworkConstants.GENESIS
        try:
            prev_hash = self.get_hash(height - 1)
        except:
            return False
        if prev_hash != header.get('prev_block_hash'):
            self.print_error("bad hash", height, prev_hash, header.get('prev_block_hash'))
            return False
        target = self.get_target(util.ub_height_to_index(height) - 1,util.IsProofOfStake(header["version"]))
        try:
            self.verify_header(header, prev_hash, target)
        except BaseException as e:
            return False
        return True

    def connect_chunk(self, idx, hexdata):
        try:
            data = bfh(hexdata)
            self.verify_chunk(idx, data)
            #self.print_error("validated chunk %d" % idx)
            self.save_chunk(idx, data)
            return True
        except BaseException as e:
            self.print_error('verify_chunk failed', str(e))
            return False

    def get_checkpoints(self):
        # for each chunk, store the hash of the last block and the target after the chunk
        cp = []
        second_fork_height = util.ForkData.second_fork_height
        second_fork_index = util.ForkData.second_fork_max_index
        if self.height() <=util.ForkData.superblockendnumber:
            n = self.height() // 2016
            for index in range(n):
                h = self.get_hash((index+1) * 2016 -1)
                target = self.get_target(index)
                cp.append((h, target))
            return cp
        elif self.height()<util.ForkData.second_fork_height:
            n = util.ForkData.pre_fork_max_index + ((self.height()-util.ForkData.fork_height) // util.ForkData.after_chunk_size)
            for index in range(n):
                if n <util.ForkData.pre_fork_max_index:
                    h = self.get_hash((index + 1) * 2016 - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
                else:
                    h = self.get_hash(util.ForkData.fork_height + (index - util.ForkData.pre_fork_max_index ) * util.ForkData.after_chunk_size - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
            return cp
        elif self.height() <util.ForkData.third_fork_height:
            n = second_fork_index + ((self.height() - second_fork_height) // util.ForkData.second_fork_chunk_size)
            for index in range(n):
                if n <util.ForkData.pre_fork_max_index:
                    h = self.get_hash((index + 1) * 2016 - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
                elif n <=second_fork_index -1:
                    h = self.get_hash(util.ForkData.fork_height + (index - util.ForkData.pre_fork_max_index ) * util.ForkData.after_chunk_size - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
                else:
                    h = self.get_hash(second_fork_height + (index - second_fork_index) * util.ForkData.second_fork_chunk_size - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
            return cp
        else:
            n = util.ForkData.third_fork_max_index + ((self.height() - second_fork_height) // util.ForkData.second_fork_chunk_size)
            for index in range(n):
                if n < util.ForkData.pre_fork_max_index:
                    h = self.get_hash((index + 1) * 2016 - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
                elif n <= second_fork_index - 1:
                    h = self.get_hash(util.ForkData.fork_height + (
                    index - util.ForkData.pre_fork_max_index) * util.ForkData.after_chunk_size - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
                elif n :
                    h = self.get_hash(
                        second_fork_height + (index - second_fork_index) * util.ForkData.second_fork_chunk_size - 1)
                    target = self.get_target(index)
                    cp.append((h, target))
                else:
                    if index == util.ForkData.third_fork_max_index:
                        continue
                    header = self.read_header(util.ub_start_height_of_index(index))
                    h = header["prev_block_hash"]
                    target = self.target_to_bits(header["bits"])
                    cp.append((h, target))
            return cp
