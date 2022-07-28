from unicodedata import category
import torch
from tqdm.notebook import tqdm
import numpy as np 
import pandas as pd
from transformers import BertTokenizer
from torch.utils.data import TensorDataset
from transformers import BertForSequenceClassification
from transformers import AdamW, get_linear_schedule_with_warmup
import numpy as np
from sklearn.metrics import f1_score
import random
import torch
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, RandomSampler, SequentialSampler



#getting unique categores this will be the first function
def pretraining(df):
    possible_labels = df.category.unique()
    label_dict = {}
    for index, possible_label in enumerate(possible_labels):
        label_dict[possible_label] = index
    category_dict = {}
    count = 0
    for category in label_dict.keys():
        category_dict[count] = category
        count = count + 1
    return category_dict


def training(df):
    possible_labels = df.category.unique()
    label_dict = {}
    for index, possible_label in enumerate(possible_labels):
        label_dict[possible_label] = index
    df.category = df['category'].map(label_dict)
    X_train, X_val, y_train, y_val = train_test_split(df.index.values, 
                                                    df.category.values, 
                                                    test_size=0.15, 
                                                    random_state=42,
                                                    stratify=df.category.values)
    df['data_type'] = ['not_set']*df.shape[0]
    df.loc[X_train, 'data_type'] = 'train'
    df.loc[X_val, 'data_type'] = 'val'
    df.groupby(['category', 'data_type']).count()

    tokenizer = BertTokenizer.from_pretrained(
        'bert-base-uncased',
        do_lower_case=True
    )

    encoded_data_train = tokenizer.batch_encode_plus(
        
        df[df.data_type=='train'].question.values,
        add_special_tokens=True,
        return_attention_mask=True,
        pad_to_max_length=True,
        max_length=256,
        return_tensors='pt'
    )

    encoded_data_val = tokenizer.batch_encode_plus(
        df[df.data_type=='val'].question.values,
        add_special_tokens=True,
        return_attention_mask=True,
        pad_to_max_length=True,
        max_length=256,
        return_tensors='pt'
    )

    input_ids_train = encoded_data_train['input_ids']
    attention_masks_train = encoded_data_train['attention_mask']
    labels_train = torch.tensor(df[df.data_type=='train'].category.values)

    input_ids_val = encoded_data_val['input_ids']
    attention_masks_val = encoded_data_val['attention_mask']
    labels_val = torch.tensor(df[df.data_type=='val'].category.values)

    dataset_train = TensorDataset(input_ids_train, 
                                attention_masks_train,
                                labels_train)

    dataset_val = TensorDataset(input_ids_val, 
                                attention_masks_val,
                            labels_val)
    model = BertForSequenceClassification.from_pretrained(
                                        'bert-base-uncased', 
                                        num_labels = len(label_dict),
                                        output_attentions = False,
                                        output_hidden_states = False
                                        )
    batch_size = 4

    dataloader_train = DataLoader(
        dataset_train,
        sampler=RandomSampler(dataset_train),
        batch_size=batch_size
    )

    dataloader_val = DataLoader(
        dataset_val,
        sampler=RandomSampler(dataset_val),
        batch_size=32
    )
    optimizer = AdamW(
        model.parameters(),
        lr = 1e-5,
        eps = 1e-8
    )
    epochs = 8

    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=0,
        num_training_steps = len(dataloader_train)*epochs
    )
    def f1_score_func(preds, labels):
        preds_flat = np.argmax(preds, axis=1).flatten()
        labels_flat = labels.flatten()
        return f1_score(labels_flat, preds_flat, average = 'weighted')

    def accuracy_per_class(preds, labels):
        label_dict_inverse = {v: k for k, v in label_dict.items()}
        
        preds_flat = np.argmax(preds, axis=1).flatten()
        labels_flat = labels.flatten()
        
        for label in np.unique(labels_flat):
            y_preds = preds_flat[labels_flat==label]
            y_true = labels_flat[labels_flat==label]
            print(f'Class: {label_dict_inverse[label]}')
            print(f'Accuracy:{len(y_preds[y_preds==label])}/{len(y_true)}\n')
            

    seed_val = 17
    random.seed(seed_val)
    np.random.seed(seed_val)
    torch.manual_seed(seed_val)
    torch.cuda.manual_seed_all(seed_val)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    #model.to(device)
    print(device)
    def evaluate(dataloader_val):

        model.eval()
        
        loss_val_total = 0
        predictions, true_vals = [], []
        
        for batch in tqdm(dataloader_val):
            
            batch = tuple(b.to(device) for b in batch)
            
            inputs = {'input_ids':      batch[0],
                    'attention_mask': batch[1],
                    'labels':         batch[2],
                    }

            with torch.no_grad():        
                outputs = model(**inputs)
                
            loss = outputs[0]
            logits = outputs[1]
            loss_val_total += loss.item()

            logits = logits.detach().cpu().numpy()
            label_ids = inputs['labels'].cpu().numpy()
            predictions.append(logits)
            true_vals.append(label_ids)
        
        loss_val_avg = loss_val_total/len(dataloader_val) 
        
        predictions = np.concatenate(predictions, axis=0)
        true_vals = np.concatenate(true_vals, axis=0)
                
        return loss_val_avg, predictions, true_vals
    for epoch in tqdm(range(1, epochs+1)):
        model.train()
        loss_train_total = 0
        
        progress_bar = tqdm(dataloader_train, 
                            desc='Epoch {:1d}'.format(epoch), 
                            leave=False, 
                            disable=False)
        
        for batch in progress_bar:
            model.zero_grad()
            batch = tuple(b.to(device) for b in batch)
            inputs = {
                'input_ids': batch[0],
                'attention_mask': batch[1],
                'labels': batch[2]
            }
            
            outputs = model(**inputs)
            loss = outputs[0]
            loss_train_total +=loss.item()
            loss.backward()
            
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            
            optimizer.step()
            scheduler.step()
            
            progress_bar.set_postfix({'training_loss': '{:.3f}'.format(loss.item()/len(batch))})     
        
        torch.save(model.state_dict(), f'BERT_ft_Epoch{epoch}.model')
        
        tqdm.write('\nEpoch {epoch}')
        
        loss_train_avg = loss_train_total/len(dataloader_train)
        tqdm.write(f'Training loss: {loss_train_avg}')
        
        val_loss, predictions, true_vals = evaluate(dataloader_val)
        val_f1 = f1_score_func(predictions, true_vals)
        tqdm.write(f'Validation loss: {val_loss}')
        tqdm.write(f'F1 Score (weighted): {val_f1}')
    torch.save(model,'AskTalosBertModel999')


data = pd.read_csv("asktalos_dataset.csv", encoding='cp1252')
categories = pretraining(data) #training model
training = training(data)



##########################testing###############################################33

import shutup
import warnings
import time
from transformers import BertTokenizer
import torch
from transformers import BertForSequenceClassification
from transformers import logging
import pandas as pd

tokenizer = BertTokenizer.from_pretrained(
    'bert-base-uncased',
    do_lower_case=True
    )
model = BertForSequenceClassification.from_pretrained(
                                  'bert-base-uncased', 
                                  num_labels = len(labels),
                                  output_attentions = False,
                                  output_hidden_states = False
                                 )
model = torch.load('AskTalosBertModel')

def prediction_bert_category(arr):
    start = time.time()
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings('ignore')
    shutup.please()
    logging.set_verbosity_error()
    arr = arr['question']
    tokenizer = BertTokenizer.from_pretrained(
    'bert-base-uncased',
    do_lower_case=True
    )
    model = BertForSequenceClassification.from_pretrained(
                                      'bert-base-uncased', 
                                      num_labels = len(labels),
                                      output_attentions = False,
                                      output_hidden_states = False
                                     )
    model = torch.load('AskTalosBertModel4')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    #print(device)
    model.to(device)
    finalcategory = []
    for i in range(len(arr)):
        review_text = arr[i]
        encoded_review = tokenizer.encode_plus(
        review_text,
        max_length=256,
        add_special_tokens=True,
        return_token_type_ids=False,
        pad_to_max_length=True,
        return_attention_mask=True,
        return_tensors='pt',
        )
        #print(encoded_review)
        input_ids = encoded_review['input_ids'].to(device)
        #print(input_ids)
        attention_mask = encoded_review['attention_mask'].to(device)
        #print(model)
        output = model(input_ids, attention_mask)
        #print(f"output is {output}")
        final_pred = torch.tensor(output.logits)
        #print(final_pred)
        sentimentIndex = int(torch.argmax(final_pred))
        #print("sentimentIndex",sentimentIndex)
        finalcategoryiment = categories[sentimentIndex]
        finalcategory.append(finalcategoryiment)
    return finalcategory






data = pd.read_csv("asktalos_dataset.csv",encoding='cp1252') #getting data for testing 
OutputPredictiction = prediction_bert_category(data) #testing models
print(OutputPredictiction) #printing predictions
