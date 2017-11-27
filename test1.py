#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import torch
import torch.nn as nn
from torch.autograd import Variable
from PIL import Image
from torch.utils.data import Dataset, DataLoader
import time
import json
from model import load_model
from config import data_transforms
import pickle
import csv



arch = 'resnet18'
pretrained = 'imagenet'
phases = ['val']
use_gpu = torch.cuda.is_available()
batch_size = 32
INPUT_WORKERS = 32
checkpoint_filename = arch + '_' + pretrained
best_check = 'checkpoint/' + checkpoint_filename + '_best.pth.tar' #tar
input_size = 224 #[224, 256, 384, 480, 640] 
train_scale = 224
test_scale = 224
AdaptiveAvgPool = True


model_conv = load_model(arch, pretrained, use_gpu=use_gpu,AdaptiveAvgPool=AdaptiveAvgPool)
for param in model_conv.parameters():
    param.requires_grad = False #节省显存

best_checkpoint = torch.load(best_check)
if use_gpu:
    if arch.lower().startswith('alexnet') or arch.lower().startswith('vgg'):
        model_conv.features = nn.DataParallel(model_conv.features)
        model_conv.cuda()
        model_conv.load_state_dict(best_checkpoint['state_dict']) 
    else:
        model_conv = nn.DataParallel(model_conv).cuda()
        model_conv.load_state_dict(best_checkpoint['state_dict']) 

if phases[0] == 'test_A':
    test_root = 'data/test_A'
elif phases[0] == 'test_B':
    test_root = 'data/test_B'
elif phases[0] == 'val':
    test_root = 'data/validation_folder'
    
with open(test_root+'/pig_test_annotations.json', 'r') as f: #label文件, 测试的是我自己生成的
    label_raw_test = json.load(f)
    
def write_to_csv(aug_softmax): #aug_softmax[img_name_raw[item]] = temp[item,:]
    with open('result/'+ phases[0] +'_1.csv', 'w', encoding='utf-8') as csvfile:
        spamwriter = csv.writer(csvfile,dialect='excel')
        for item in aug_softmax.keys():
            the_sum = sum(aug_softmax[item])
            for c in range(0,30):
                spamwriter.writerow([int(item.split('.')[0]), c+1, aug_softmax[item][c]/the_sum])


class SceneDataset(Dataset):

    def __init__(self, json_labels, root_dir, transform=None):
        self.label_raw = json_labels
        self.root_dir = root_dir
        self.transform = transform

    def __len__(self):
        return len(self.label_raw)

    def __getitem__(self, idx):
        if phases[0] == 'val':
            img_name = self.root_dir+ '/' + str(self.label_raw[idx]['label_id']+1) + '/'+ self.label_raw[idx]['image_id']
        else:
            img_name = os.path.join(self.root_dir, self.label_raw[idx]['image_id'])
        img_name_raw = self.label_raw[idx]['image_id']
        image = Image.open(img_name)
        label = int(self.label_raw[idx]['label_id'])

        if self.transform:
            image = self.transform(image)

        return image, label, img_name_raw


transformed_dataset_test = SceneDataset(json_labels=label_raw_test,
                                        root_dir=test_root,
                                           transform=data_transforms('test',input_size, train_scale, test_scale)
                                           )           
dataloader = {phases[0]:DataLoader(transformed_dataset_test, batch_size=batch_size,shuffle=False, num_workers=INPUT_WORKERS)
             }
dataset_sizes = {phases[0]: len(label_raw_test)}


class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count
        
def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k
    output: logits
    target: labels
    """
    maxk = max(topk)
    batch_size = target.size(0)

    _, pred = output.topk(maxk, 1, True, True)
    pred = pred.t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))

    res = []
    for k in topk:
        correct_k = correct[:k].view(-1).float().sum(0, keepdim=True)
        res.append(correct_k.mul_(100.0 / batch_size))
        

    pred_list = pred.tolist()  #[[14, 13], [72, 15], [74, 11]]
    return res, pred_list

def batch_to_list_of_dicts(indices, image_ids):  #indices2 是预测的labels
    '''
    pred_list = pred.tolist()  #[[14, 13], [72, 15], [74, 11]]
    print(img_name_raw) #('ed531a55d4887dc287119c3f6ebf7eb162bed6cf.jpg', '520036616eb2594b6e9d41b0415deea607e8de12.jpg')
    '''
    result = [] #[{"image_id":"a0563eadd9ef79fcc137e1c60be29f2f3c9a65ea.jpg","label_id": [5,18,32]}]
    dict_ = {}
    for item in range(len(image_ids)):
        dict_ ['image_id'] = image_ids[item]
        dict_['label_id'] = [indices[0][item], indices[1][item], indices[2][item]]
        result.append(dict_)
        dict_ = {}
    return result

my_aug_softmax2 = {}
def test_model (model, criterion):
    since = time.time()

    mystep = 0    

    for phase in phases:
        
        model.train(False)  # Set model to evaluate mode

        top1 = AverageMeter()
        top3 = AverageMeter()
        loss1 = AverageMeter()
        results = []
        aug_softmax = {}

        # Iterate over data.
        for data in dataloader[phase]:
            # get the inputs
            mystep = mystep + 1
            if(mystep%10 ==0):
                duration = time.time() - since
                print('step %d vs %d in %.0f s' % (mystep, total_steps, duration))

            inputs, labels, img_name_raw= data
            #print(img_name_raw) #('ed531a55d4887dc287119c3f6ebf7eb162bed6cf.jpg', '520036616eb2594b6e9d41b0415deea607e8de12.jpg')

            # wrap them in Variable
            if use_gpu:
                inputs = Variable(inputs.cuda())
                labels = Variable(labels.cuda())
            else:
                inputs, labels = Variable(inputs), Variable(labels)

            # forward
            outputs = model(inputs)
            crop_softmax = nn.functional.softmax(outputs)
            temp = crop_softmax.cpu().data.numpy()
            for item in range(len(img_name_raw)):
                aug_softmax[img_name_raw[item]] = temp[item,:] #防止多线程啥的改变了图片顺序，还是按照id保存比较保险
                
            _, preds = torch.max(outputs.data, 1)
            loss = criterion(outputs, labels)

#            # statistics
            res, pred_list = accuracy(outputs.data, labels.data, topk=(1, 3))
            prec1 = res[0]
            prec3 = res[1]
            top1.update(prec1[0], inputs.data.size(0))
            top3.update(prec3[0], inputs.data.size(0))
            loss1.update(loss.data[0], inputs.size(0))
            
            results += batch_to_list_of_dicts(pred_list, img_name_raw)


        print(' * Prec@1 {top1.avg:.6f} Prec@3 {top3.avg:.6f} Loss@1 {loss1.avg:.6f}'.format(top1=top1, top3=top3, loss1=loss1))
        
        with open(('result/%s_submit1_%s.json'%(checkpoint_filename, phase)), 'w') as f:
            json.dump(results, f)
        
        with open(('result/%s_softmax1_%s.txt'%(checkpoint_filename, phase)), 'wb') as handle:
            pickle.dump(aug_softmax, handle)
        
        if phases[0] != 'val':
            write_to_csv(aug_softmax)
    return 0



criterion = nn.CrossEntropyLoss()


######################################################################
# val and test
total_steps = 1.0  * len(label_raw_test) / batch_size
print(total_steps)
test_model(model_conv, criterion)
